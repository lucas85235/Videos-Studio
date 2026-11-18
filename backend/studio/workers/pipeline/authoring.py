"""Etapas de autoria: geram/alteram o documento do projeto usando a cadeia de LLM."""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from studio.config import get_settings
from studio.core import sources as tiers
from studio.core.glossary import GlossaryEntry, check_pairs, glossary_prompt
from studio.core.hashing import seed_from
from studio.core.numbers import card_number, notable_number_runs, number_runs
from studio.core.project import characters as characters_mod
from studio.core.project.document import (
    BEAT_ROLES,
    CARD_KINDS,
    DATA_KINDS,
    GAG_PAYOFFS,
    PRESENTER_AT_RE,
    REAL_KINDS,
    THUMB_WORDS_MAX,
    ArtDirection,
    BeatMark,
    BgLayer,
    CardItem,
    Character,
    Entity,
    GagHint,
    MediaRef,
    Metadata,
    PackageCandidate,
    Presenter,
    ProjectDoc,
    RealHint,
    Scene,
    SceneCard,
    ScenePresenter,
    Segment,
    Source,
    ThumbLayers,
    Visual,
    effective_allow_editorial,
)
from studio.core.project.pool import Pool, PoolItem, load_pool
from studio.core.regions import find_regions, is_us_state, region_code, region_codes
from studio.core.styles.library import StyleNotFound, get_style
from studio.core.styles.schema import (
    AUTO_CAMERA_MOVES,
    CHART_TYPES,
    GRADE_PRESETS,
    Family,
    SourcePolicy,
    Structure,
    StyleSpec,
    grammar_for,
    sources_for,
)
from studio.core.styles.visual import (
    APPROACHES,
    COMPOSITIONS,
    FOCUS_POINTS,
    FOCUS_ROTATION,
    GENRES,
    SHOT_ROTATION,
    SHOTS,
    STYLE_FITS,
    catalog_text,
    clean_id,
    clean_languages,
    compositions_for_caption,
    house_language,
    pick,
)
from studio.core.text import estimate_seconds, normalize_token, split_beats
from studio.media import thumb_variety
from studio.providers import registry
from studio.providers.base import LLMResult
from studio.workers.analytics.lessons import lessons_block, lessons_key
from studio.workers.pipeline import characters as character_scenes
from studio.workers.pipeline import dubbing, media_pool, storyboard_check
from studio.workers.pipeline import presenter as presenter_scenes
from studio.workers.pipeline.schemas import (
    METADATA_SCHEMA,
    PACKAGE_SCHEMA,
    PRESENTER_HINT_FIELDS,
    SCRIPT_SCHEMA,
    STORYBOARD_KINDS,
    STORYBOARD_SCHEMA,
    STORYBOARD_SOURCES,
    TRANSLATE_SCHEMA,
)
from studio.workers.pipeline.storyboard_check import CORRIGIDO, Beat, Issue, has_real

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


def reference_voice_block(style: StyleSpec) -> str:
    """Bloco "Voz e estrutura da referência" (ADR 0004 §3.3): só existe quando o estilo saiu de uma referência com
    perfil de edição (`edit.grammar.profile_refs`) e o perfil mediu fala, tom, estrutura ou humor."""
    refs = style.edit.grammar.profile_refs
    if not refs:
        return ""
    from studio.core.editing.grammar import reference_profiles, script_reference_block

    try:
        return script_reference_block(reference_profiles(refs), style.format.duration.target_s)
    except Exception as exc:  # noqa: BLE001 - sem o perfil, o roteiro segue como antes
        log.warning("perfil da referência indisponível para o roteiro: %s", exc)
        return ""


def generate_script(doc: ProjectDoc, style: StyleSpec, *, project_id: str | None = None, job_id: str | None = None,
                    persona: str = "", today: date | None = None,
                    lessons: list[dict[str, Any]] | None = None) -> tuple[ProjectDoc, str]:
    """Roteiro do tema. `today` (padrão: a data de hoje) entra no pedido para o roteiro situar datas próximas.
    `lessons` (lições aplicadas do projeto, já filtradas e no orçamento) entram no fim do pedido."""
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
    voice_block = reference_voice_block(style)
    if voice_block:
        prompt += "\n" + voice_block
    package_block = locked_package_block(doc, style)
    if package_block:
        prompt += "\n" + package_block
    if lessons:
        prompt += "\n" + lessons_block(lessons, stage="roteiro")
    context = {
        "topic": doc.topic or doc.title,
        "structure": structure,
        "target_words": target_words,
        "cta": style.script.cta,
        "structure_kind": style.structure.value,
        # muda com a lista de lições: o pedido não é o mesmo de antes
        "lessons_key": lessons_key(lessons or []),
    }
    if package_block:
        context["package"] = package_block  # o pedido muda com o pacote travado
    if voice_block:
        context["reference_voice"] = voice_block  # e com o perfil da referência
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
    if data.get("title") and not package_block:
        # Pacote travado: o título é o do candidato escolhido e o roteiro não o troca.
        new_doc.title = data["title"].strip()[:200]
    new_doc.sources = [Source(**s) for s in data.get("sources", []) if s.get("title")]
    return ProjectDoc.model_validate(new_doc.model_dump()), provider_id


STORYBOARD_SYSTEM = """Você é o diretor de arte e o editor de um canal sem rosto (Shorts, TikTok, Reels e vídeos longos).
O roteiro já foi aprovado e chega cortado em batidas (uma frase ou ideia por batida). Sua tarefa é a
curadoria visual: decidir, batida por batida, o que aparece na tela usando só o que o app sabe produzir. O MATERIAL
REAL vem primeiro (o acervo do projeto: trechos oficiais, capturas de página e notícia, fotos próprias e livres); a
imagem de IA é apoio declarado, com motivo, e o teto dela é medido em tempo de tela.

1) Direção de arte ("direction"), decidida pelo assunto e pelo roteiro, não pelo nome do estilo:
- genre: o tipo de vídeo (notícia de games não é mistério, mesmo com estilo de mistério).
- approach: literal (mostra o assunto), misto ou alegorico (metáforas).
- world: só o cenário, lugar e época, em até 20 palavras em inglês ("busy port city, 1920s"), sem hora do dia,
  clima nem céu (cada imagem diz os seus). O app junta o world às imagens de IA que citam entidades: nada de pessoas,
  objetos ou ações nele. Lugar só quando o roteiro se passa num lugar específico: o país do canal ou do público não é
  o lugar do assunto. Sem pedido do roteiro, nada de país, nacionalidade, bandeira ou cartão-postal (monumento,
  paisagem-símbolo) no world, nos looks, no hook nem nos prompts. Personagem, lugar ou objeto que volta em várias
  batidas vira entidade (só entra nas imagens que a citam); o que é de uma batida só vai no prompt dela.
- languages: 1 a 3 ids do catálogo. O primeiro é o estilo de ilustração de TODAS as imagens de IA do vídeo (o app
  aplica o mesmo em todas; nunca ui_mockup); os outros só servem quando o usuário pede outra imagem. Prefira os do
  estilo; se não combinarem com o assunto, escolha os que combinam e diga em style_fit/style_note.
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

2) Uma entrada em "scenes" por batida, com a chave exata e na mesma ordem.
De onde vem a imagem (source). Quando o pedido traz o "Acervo de material real" (linhas "id · nível · tipo ·
descrição · duração"), toda batida de imagem segue esta ordem de preferência:
  a) source "pool" com o item ESPECÍFICO da batida (pool_id = o id exato da lista: a pessoa, o lugar, o produto, o
     trecho do trailer, a página ou a notícia que a frase cita);
  b) source "pool" com um item GENÉRICO do mesmo assunto (cidade, tela, multidão, cenário);
  c) um cartão (stat, compare, title, text, quote) quando a batida é número, citação ou frase de impacto;
  d) só então source "new" (imagem de IA), e só com ai_reason: o motivo, em português e curto, de a batida não ter
     material real: hipótese ("e se..."), futuro, conceito abstrato ou cena impossível de filmar. Sem motivo, não
     use "new".
  O mesmo item real pode voltar em outra batida; a mesma imagem de IA não serve de enchimento. ai_reason é null fora
  de "new". Sem acervo no pedido, use "new" e "reuse" dentro do limite (com ai_reason quando houver motivo).
O que o app desenha:
- image: imagem (ou o trecho de vídeo, quando o item do acervo é clipe) em tela cheia com câmera. source "pool" = item
  do acervo (pool_id); source "new" = imagem nova de IA (~35 s de cota cada; respeite o limite); source "reuse" = a
  imagem de uma batida anterior (reuse_key) com outro enquadramento (focus), custo zero.
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
Todo cartão (stat, compare, title, text, quote) usa de fundo, desfocado e escurecido, o material de uma batida
vizinha: prefira o real (source "pool" com o pool_id, ou "reuse" de uma batida com item do acervo); source "none"
deixa o fundo liso (só quando o cartão pede fundo limpo); fundo de IA só quando não houver material real.
Em qualquer cena: headline (faixa no topo, até 5 palavras), stamp (carimbo, até 2 palavras: só o veredito sobre o que
a cena afirma, ex. "CONFIRMADO", "BOATO", "FALSO", "RECORDE", e só onde o veredito importa; nunca rótulo: tipo ou nome
da fonte ("IMPRENSA"), data ("13/11") nem numeração de lista ("SINAL 2"); nunca "INÉDITO" nem "EXCLUSIVO": as
imagens são ilustrações), source_ref (o número da fonte em "Fontes do roteiro" que
traz o dado do cartão, ou null). Campo que não se aplica vai como null. A faixa compete com a legenda: use no
gancho e só onde acrescenta o que a fala não diz (data, nome, virada de assunto: "TRAILER 2 · MAIO 2025", não só
"MAIO DE 2025"), no máximo 1 a cada 3 cenas, sem repetir a fala da batida.

Mídia real de reserva (real_kind, real_query, real_note, real_source_ref): quando o acervo não tem o que a batida
pede, marque as batidas em que uma imagem real vale mais que a de IA: pessoa real citada, lugar real, produto,
personagem oficial, notícia ou anúncio, documento. O app busca fotos livres (Wikimedia Commons) para elas.
- real_kind: photo (pessoa, lugar ou objeto real que pode ter foto livre no Wikimedia Commons), news (a batida cita
  uma notícia ou anúncio: vira cartão de notícia; real_source_ref = o número da fonte com o link, ou null), official
  (arte, trailer ou foto de imprensa oficial: só o usuário pode anexar).
- real_query: 2 a 6 palavras em inglês para a busca (nome próprio vale: "Miami Beach skyline"); null quando não há o
  que buscar.
- real_note: em português, o que mostrar e onde achar ("arte oficial da Lucia no Newswire da Rockstar").
- Só em kind image fora de source "pool" (o item do acervo já é real). Sem mídia real: os quatro null.
- A cena continua com o prompt de IA de reserva (o vídeo funciona sem a mídia real). Com real_kind official de
  personagem, a reserva mostra de costas, em silhueta ou só objetos e lugar, nunca o rosto.

Marcas de edição (todas as batidas; o app monta cortes, ênfases e efeitos a partir delas):
- role: hook (o gancho, a 1ª batida), reveal (revelação), joke (piada), aside (comentário à parte), list (item de
  lista), number (número que importa), tension (suspense), breath (respiro, passagem), cta (chamada final).
- emphasis: 1 a 3 palavras FALADAS na batida, escritas como na frase (o número, o nome, a palavra-chave): viram a
  ênfase da legenda e o ponto de corte; [] quando nada se destaca.
- serious: true na batida séria (morte, tragédia, crime, doença, perda): nada cômico nela.
- gag_payoff, gag_line, gag_at: a piada da batida, quando houver. Meme é reação à frase (contradição, literalidade,
  autoironia), nunca decoração; batida séria sem humor. gag_payoff: meme | freeze | zoom | sfx | text; gag_line:
  a reação em até 6 palavras (o "meme sem meme"); gag_at: a palavra falada onde a piada cai. Sem piada: os três null.
  No máximo 1 piada a cada 4 batidas.

Vídeo IA (video_motion): o app pode animar a imagem de poucas batidas com um clipe de IA (pago, o usuário confirma).
Marque só as batidas de imagem nova em que o movimento conta a história (o gancho, uma virada, uma ação: fumaça
subindo, ondas, multidão andando, a câmera avançando por um corredor), no máximo o limite do pedido. video_motion:
o movimento em inglês, até 25 palavras ("slow push-in as fog rolls over the harbor"), sem estilo nem texto. Nas
outras batidas (e com limite 0): null.

Regras:
1. Mostre o sujeito concreto da batida (pessoa, lugar, objeto, ação, na época real do assunto). Metáfora só
   quando a frase não tiver nada concreto, no máximo 1 a cada 5 imagens.
2. Número, data, valor, recorde ou comparação vira stat ou compare, ou vai em headline/stamp, sempre com
   algarismos. Nunca peça número ou texto dentro da imagem. Use só números da narração ou das fontes.
3. A primeira batida é o gancho: image com o melhor item próprio, oficial, captura ou foto livre do acervo (source
   "pool", o mais forte e literal do assunto), headline curta, motion "punch_zoom". Sem acervo: source "new", a imagem
   mais forte e literal do assunto.
4. Quando a narração volta ao MESMO momento, lugar, personagem ou objeto, use reuse em vez de outra imagem
   parecida, com outro focus e motion "zoom_to". Batida com outra data, outro evento ou outra headline nunca
   reaproveita imagem em tela cheia (kind image): use imagem nova. A chamada final pode reaproveitar o gancho.
   Espalhe o reaproveitamento: no máximo 3 batidas seguidas na mesma imagem, contando os cartões sobre ela;
   depois alterne com imagem nova ou mídia real.
5. No máximo 2 cartões (stat, compare, title, text, quote) seguidos; não repita o shot em imagens novas seguidas.
6. prompt (só imagem nova de IA): 1 a 3 frases em inglês com sujeito, ação, lugar, época, hora e luz. Cada imagem
   nova diz a própria hora e luz, e elas variam: no máximo 1 em cada 3 no mesmo momento (ex.: pôr do sol); siga o
   clima da narração (passado = luz fria ou de arquivo, revelação = escuro). Tela dentro da imagem (TV, celular,
   telão) não repete o mesmo conteúdo. NÃO escreva estilo, técnica, paleta, "no text" nem "vertical": o app
   acrescenta. Só no prompt de IA (o material real do acervo mostra o assunto de verdade): nada de logotipo,
   personagem oficial, tipografia de marca, interface de jogo, "screenshot", "gameplay", "leak" ou cena de trailer;
   nome de personagem de ficção, marca, jogo, produto ou pessoa viva não entra no prompt, no world, no hook nem no
   look: descreva o que se vê (figura histórica, cidade e país reais podem); personagem oficial de ficção aparece de
   costas, em silhueta ou só por objetos, nunca de frente. Na imagem nova, preencha também language (a primeira da
   direção), shot, composition e entities (ids da direção que aparecem nela).
7. composition: deixe livre a faixa da legenda informada no pedido.
8. A imagem de IA não se passa por foto nem por captura, e não troca de estilo: todas usam a primeira linguagem da
   direção (o app aplica; o language da cena é ignorado). Nada de janela de navegador, tela de app, cursor de mouse
   ou moldura de aparelho falsos na imagem de IA: interface de verdade é captura do acervo ou cartão."""

# Regras dos cartões de gráfico e mapa, só no pedido de estilo da família de dados e geografia.
STORYBOARD_DATA_RULES = """Estilo de dados e geografia: além de stat e compare, o app desenha gráficos e mapas animados.
- chart: gráfico com 3 a 8 itens (label, value), todos medidos do mesmo jeito e na ordem do eixo. chart = "line"
  para série no tempo (label = ano ou mês, em ordem), "bar" para categorias, "pie" ou "donut" para partes de um
  todo (valores em %, somando perto de 100). label (título do cartão) diz o que é medido e a unidade; highlight é
  o índice do item em destaque (ex.: o último ano). Só números da narração ou das fontes, nunca inventados.
- map: mapa com 1 a 6 países ou estados dos EUA destacados; em cada item, region = código ISO de 3 letras do país
  ("BRA", "USA") ou "US-" + sigla do estado ("US-TX", "US-CA"; nunca só "CA" ou "TX") ou o nome do lugar, label
  curto (ou vazio para o nome do lugar) e value opcional ("214 MI"). label (título) diz o que o mapa mostra;
  highlight é o lugar principal. Use quando a batida fala de onde algo acontece, de rota, fronteira ou comparação
  entre lugares. O mapa ocupa a tela inteira: não usa imagem de fundo (source "none").
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
_MOTIONS = dict.fromkeys(AUTO_CAMERA_MOVES)  # parallax só por escolha do usuário


def image_budget(n_beats: int, style: StyleSpec, pool: Pool | None = None) -> int:
    """Limite de imagens novas de IA do storyboard (o resto reaproveita imagem ou é cartão).

    Com acervo de material real, o limite sai do teto de IA da política de fontes (`ai_max_share`, a fração das
    batidas), sem o mínimo de 3: o real ocupa o resto. Sem acervo, o de sempre (`new_image_ratio`, no mínimo 3)."""
    legacy = max(3, math.ceil(n_beats * style.edit.new_image_ratio))
    if not has_real(pool):
        return legacy
    return min(legacy, math.ceil(n_beats * sources_for(style).ai_max_share - 1e-9))


# --- acervo de material real (ADR 0004) ----------------------------------------------------------

POOL_MAX_LINES = 120
# Níveis do acervo que entram como reserva de outra cena (o meme é reação, não serve de B-roll).
_FALLBACK_TIERS = [t for t in tiers.FALLBACK if t != "meme"]
_MATCH_MIN_LEN = 4
_MATCH_STOP = {
    "para", "pela", "pelo", "como", "mais", "muito", "isso", "esse", "essa", "este", "esta", "aqui", "onde", "quando",
    "ainda", "depois", "antes", "sobre", "entre", "todo", "toda", "todos", "todas", "ninguem", "agora", "cada", "porque",
    "the", "with", "from", "that", "this", "into", "over", "their", "there", "what", "when", "where", "which",
}  # fmt: skip


def _match_tokens(*texts: object) -> set[str]:
    out: set[str] = set()
    for text in texts:
        if isinstance(text, str):
            for word in text.replace("_", " ").replace("-", " ").split():
                token = normalize_token(word)
                if len(token) >= _MATCH_MIN_LEN and token not in _MATCH_STOP:
                    out.add(token)
                    out.add(token.rstrip("s"))
    return out


def _item_tokens(item: PoolItem) -> set[str]:
    return _match_tokens(item.desc, *item.tags, *item.entities)


def pool_block(pool: Pool) -> str:
    """As linhas do acervo para o pedido (`media_pool.prompt_block` é o dono do formato: `id · nível · tipo · desc ·
    dur`, agrupado por tópico). Só com material real (`has_real`): acervo só de IA, gráfico ou meme não vira "real
    primeiro" e não entra no pedido."""
    if not has_real(pool):
        return ""
    return media_pool.prompt_block(pool, max_lines=POOL_MAX_LINES)


_HUMOR_TEXT = {"none": "sem humor", "light": "humor leve (reação pontual)", "meme": "humor de meme (reações frequentes)"}


def grammar_lines(style: StyleSpec) -> list[str]:
    """A "Gramática de edição deste estilo" em 3 a 5 linhas (de `grammar_for`): o ritmo e a densidade que o editor
    automático vai montar, para o storyboard escolher material e marcas que caibam nela."""
    g = grammar_for(style)
    if g.editor == "off":
        return ["Gramática de edição deste estilo: editor automático desligado (um plano por batida, sem eventos)."]

    def n(value: float) -> str:
        return f"{value:g}".replace(".", ",")

    return [
        "Gramática de edição deste estilo (o app monta cortes e efeitos a partir do material e das marcas):",
        f"- ritmo: planos de ~{n(g.shot_median_s)} s em média, nada parado mais de {n(g.max_still_s)} s; "
        f"{n(g.switch_per_min)} trocas de tipo de material por minuto.",
        f"- por minuto: {n(g.punch_per_min)} punch, {n(g.text_pop_per_min)} textos que pulam, "
        f"{n(g.sfx_per_min)} efeitos sonoros, {n(g.meme_per_min)} memes.",
        f"- humor: {_HUMOR_TEXT.get(g.humor, g.humor)}; ênfase da legenda: {g.caption_emphasis}.",
    ]


def _policy_lines(style: StyleSpec, total_s: float) -> list[str]:
    policy = sources_for(style)
    ai_pct = round(policy.ai_max_share * 100)
    lines = [
        f"Teto de imagem de IA: {ai_pct}% do tempo de tela (~{total_s * policy.ai_max_share:.0f} s de {total_s:.0f} s)"
        + (", sempre com ai_reason." if policy.require_ai_reason else ".")
        + f" Espalhe: no máximo {round(tiers.ai_window_cap(policy.ai_max_share) * 100)}% em qualquer trecho de "
        f"{tiers.AI_WINDOW_S:.0f} s.",
        f"Gancho: item de nível {', '.join(policy.hook)} do acervo.",
        f"Cada item do acervo aparece no máximo {tiers.ITEM_MAX_USES} vezes, a {tiers.ITEM_REUSE_GAP_S:.0f} s ou mais "
        "uma da outra (a foto e o cartão logo depois dela contam como uma vez só).",
        f"Material oficial (trailer, gameplay): até {round(policy.official_max_share * 100)}% do tempo, trechos de até "
        f"{_seconds_text(policy.official_max_clip_s)} s.",
    ]
    if not policy.ai_photo:
        lines.append("A IA não imita foto: linguagem assumidamente não fotográfica, variando entre as imagens de IA.")
    return lines


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
                      channel: dict[str, Any] | None = None, styles: dict[str, str] | None = None,
                      characters: list[Character] | None = None, pool: Pool | None = None) -> str:
    """Pedido do storyboard v2: contexto completo do vídeo, catálogos, a gramática de edição, o acervo de material
    real (quando há) e as batidas."""
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
        # Os personagens vão no bloco próprio (sem os caminhos das imagens âncora).
        bible = {k: v for k, v in (channel.get("style_bible") or {}).items() if k != characters_mod.BIBLE_KEY}
        if bible:
            lines.append("Identidade visual do canal: " + json.dumps(bible, ensure_ascii=False))
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
        f"Limite de vídeo IA (video_motion): {video_limit(style)} batida(s).",
        "",
        "Estilos disponíveis (id: nome):",
        *([f"- {sid}: {name}" for sid, name in (styles or {}).items()] or ["(nenhum)"]),
        "",
        catalog_text(photoreal=style.art.photoreal),
        "Gradações (grade): " + ", ".join(GRADE_PRESETS),
        "Câmera (motion): " + ", ".join(AUTO_CAMERA_MOVES) + " (zoom_to aproxima do focus; feito para reuse)",
        "",
        "Fontes do roteiro (source_ref é o número entre colchetes):",
        *([f"- [{n}] {s.title}" + (f": {s.note}" if s.note else "") + (f" ({s.url})" if s.url else "")
           for n, s in enumerate(doc.sources, 1)] or ["(nenhuma)"]),
        "",
        *grammar_lines(style),
        "",
    ]
    block = pool_block(pool) if pool is not None else ""
    if pool is not None and block:
        lines += [
            'Acervo de material real (source "pool" + pool_id; id · nível · tipo · descrição · duração):',
            block,
            "Item de nível meme, graphic ou ai não é imagem de batida (o meme é só reação, na piada): não cite em "
            "pool_id.",
            *_policy_lines(style, total),
            "",
        ]
    lines += [
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
    lines += character_scenes.prompt_lines(characters or [])
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


def _storyboard_context(doc: ProjectDoc, style: StyleSpec, beats: list[Beat], budget: int,
                        pool: Pool | None = None) -> dict[str, Any]:
    """Dados para o LLM offline (o Claude recebe só o system e o pedido). O acervo (`pool`) só entra quando há."""
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
    context: dict[str, Any] = {
               "topic": doc.topic or doc.title, "bible": style.art.bible, "languages": list(style.art.languages),
               "photoreal": style.art.photoreal, "budget": budget, "scenes": scenes, "data_geo": is_data_style(style),
               "sources": [{"n": n, "title": s.title, "url": s.url} for n, s in enumerate(doc.sources, 1)]}
    if has_real(pool):
        assert pool is not None
        policy = sources_for(style)
        context["pool"] = [{"id": i.id, "tier": i.tier, "media": i.media, "desc": i.desc, "tags": list(i.tags),
                            "entities": list(i.entities), "dur_s": i.dur_s} for i in pool.items]
        context["hook_tiers"] = list(policy.hook)
        context["ai_max_share"] = policy.ai_max_share
    return context


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


# Clichês que o gerador de imagem põe sozinho e denunciam a IA (QA de vídeo real): entram no "Avoid" de toda direção
# nova. Interface falsa sempre (interface de verdade é captura); marca de país só quando a direção não tem lugar,
# monumento ou figura real (aí o roteiro é sobre o lugar e a bandeira ou o monumento podem ser o assunto).
_AVOID_UI = ("fake browser window or app frame", "mouse cursor")
_AVOID_NATIONAL = ("national flags", "postcard landmarks")


def _direction_from(raw: object, style: StyleSpec, *, current_style_id: str, style_ids: set[str]) -> ArtDirection:
    """Direção de arte da resposta, com cada valor fora do catálogo trocado pelo padrão (e os clichês da IA no
    `avoid`)."""
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
        avoid=list(dict.fromkeys([*[t for t in (_text(x, 60) for x in _list(data.get("avoid"))) if t][:16], *_AVOID_UI,
                                  *(() if any(e.real for e in entities) else _AVOID_NATIONAL)])),
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
    """new | reuse | none | pool"""
    root: int | None = None
    """Índice da batida dona da imagem (source "reuse")."""
    item: PoolItem | None = None
    """Item do acervo que a cena mostra (source "pool")."""
    ai_reason: str = ""
    """Por que a cena é imagem de IA (source "new"); vazio: sem motivo declarado."""
    topic: set[str] = field(default_factory=set)
    """Palavras da batida (fala, prompt, busca) para achar o item do acervo do mesmo tópico."""
    texts: tuple[str, ...] = ()
    """Os textos da batida (fala, faixa, prompt, busca) para conferir se a foto de banco tem a ver com ela
    (`media_pool.fits_scene`)."""
    plain: bool = False
    """Cartão com o fundo liso pedido pela resposta (com acervo, fica liso)."""
    mark: BeatMark | None = None
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
    video: str | None = None
    """Sugestão de vídeo IA: o movimento em inglês (só em imagem nova)."""


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


def real_hint_limit(n_beats: int, ai_max_share: float) -> int:
    """Quantas sugestões de mídia real o storyboard guarda. O real deixou de ter teto próprio (ADR 0004): o limite só
    evita sugerir em tudo, e é o tempo que sobra do teto de IA (antes, 1 a cada 3 batidas)."""
    return max(2, math.ceil(n_beats * (1 - ai_max_share) - 1e-9))


def _real_rank(hint: RealHint) -> int:
    """Prioridade da sugestão quando passa do limite: notícia com fonte, material oficial, notícia, foto."""
    if hint.kind == "news" and hint.source_ref is not None:
        return 0
    return {"official": 1, "news": 2}.get(hint.kind, 3)


def _limit_real(picks: list[_Pick], ai_max_share: float) -> list[Issue]:
    hinted = [i for i, p in enumerate(picks) if p.real is not None]
    limit = real_hint_limit(len(picks), ai_max_share)
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


def _map_items(data: dict[str, Any], lang: str) -> list[tuple[int, CardItem]]:
    """Regiões do mapa que o renderer sabe desenhar (pelo código ou pelo nome, lidas juntas: "CA" ao lado de um estado
    dos EUA é a Califórnia), sem repetir, cada uma com a posição dela na resposta (para o destaque). O rótulo que só
    repete o nome da região sai (a timeline põe o nome no idioma de cada vídeo)."""
    raws = [(i, raw) for i, raw in enumerate(_list(data.get("items"))) if isinstance(raw, dict)]
    labels = [_words_max(raw.get("label"), 4) for _, raw in raws]
    codes = region_codes([(raw.get("region"), label) for (_, raw), label in zip(raws, labels, strict=True)])
    states = any(code is not None and is_us_state(code) for code in codes)
    items: list[tuple[int, CardItem]] = []
    seen: set[str] = set()
    for (index, raw), item_label, code in zip(raws, labels, codes, strict=True):
        if code is None or code in seen:
            continue
        seen.add(code)
        item_value = _text(raw.get("value"), 24)
        own_label = item_label and region_code(item_label, states=states) != code
        items.append((index, CardItem(label={lang: item_label} if own_label else {},
                                      value={lang: item_value} if item_value else {}, region=code)))
        if len(items) == MAX_ITEMS["map"]:
            break
    return items


def _chart_type(data: dict[str, Any], items: list[CardItem], lang: str) -> str:
    """Tipo do gráfico pedido; pizza e rosca só com valores positivos. Sem tipo: linha quando os rótulos são anos."""
    chart = pick(data.get("chart"), dict.fromkeys(CHART_TYPES))
    amounts = [card_number(item.value.get(lang, ""), lang) for item in items]
    if chart in ("pie", "donut") and not all(a is not None and a > 0 for a in amounts):
        chart = "bar"
    if chart is None:
        years = all(_YEAR.fullmatch(item.label.get(lang, "").strip()) for item in items)
        chart = "line" if years else "bar"
    return chart


def _card_from(data: dict[str, Any], kind: str, lang: str,
               sources: list[Source] | None = None, *, data_style: bool = True) -> tuple[str, SceneCard | None]:
    """Tipo final e cartão da cena: stat sem valor, compare sem 2 itens, gráfico sem 3 números e mapa sem região
    conhecida viram outro tipo. Fora da família de dados (`data_style=False`), gráfico vira comparação e mapa vira
    stat ou texto: o desenho vetorial não combina com o visual dos outros estilos. O destaque segue o item que a
    resposta apontou, mesmo quando itens antes dele saem."""
    value = _text(data.get("value"), 24)
    label = _words_max(data.get("label"), 6)
    if not data_style and kind == "chart":
        kind = "compare"
    if not data_style and kind == "map":
        kind = "stat" if value else "text"
    items: list[tuple[int, CardItem]] = []  # (posição na resposta, item)
    if kind == "map":
        items = _map_items(data, lang)
        if not items:
            kind = "stat" if value else "text"
    else:
        for index, raw in enumerate(_list(data.get("items"))[:MAX_ITEMS.get(kind, 3)]):
            if isinstance(raw, dict):
                item_label, item_value = _words_max(raw.get("label"), 6), _text(raw.get("value"), 24)
                if item_label or item_value:
                    items.append((index, CardItem(label={lang: item_label} if item_label else {},
                                                  value={lang: item_value} if item_value else {})))
    if kind == "chart":
        numeric = [(i, item) for i, item in items if card_number(item.value.get(lang, ""), lang) is not None]
        if len(numeric) >= 3:
            items = numeric
        else:
            kind, items = "compare", (numeric or items)[:3]
    if kind == "compare" and len(items) < 2:
        if len(items) == 1 and items[0][1].value:
            only = items[0][1]
            value, label = value or only.value[lang], label or only.label.get(lang, "")
        kind = "stat" if value else "text"
    if kind == "stat" and not value:
        kind = "text"
    wanted = data.get("highlight")
    positions = [i for i, _ in items]
    highlight = (positions.index(wanted) if isinstance(wanted, int) and not isinstance(wanted, bool)
                 and wanted in positions else None)
    cards = [item for _, item in items]
    headline = _words_max(data.get("headline"), 5)
    stamp = _words_max(data.get("stamp"), 2)
    with_items = kind in ("compare", "chart", "map")
    card = SceneCard(
        value={lang: value} if kind == "stat" and value else {},
        label={lang: label} if kind in DATA_KINDS and label else {},
        items=cards if with_items else [],
        highlight=highlight if with_items else None,
        chart=_chart_type(data, cards, lang) if kind == "chart" else None,  # type: ignore[arg-type]
        headline={lang: headline} if headline else {},
        stamp={lang: stamp} if stamp else {},
        source=_source_of(data.get("source_ref"), sources or []),
    )
    return kind, (card if card.resolved(lang) else None)


def _next_shot(previous: str | None) -> str:
    if previous not in SHOT_ROTATION:
        return SHOT_ROTATION[0]
    return SHOT_ROTATION[(SHOT_ROTATION.index(previous) + 1) % len(SHOT_ROTATION)]


# --- marcas de edição da batida (ADR 0004) -------------------------------------------------------

_ROLES = dict.fromkeys(BEAT_ROLES)
_PAYOFFS = dict.fromkeys(GAG_PAYOFFS)
_ROLE_ENERGY = {"hook": 3, "reveal": 3, "tension": 2, "joke": 2, "number": 2, "list": 1, "aside": 1, "cta": 1,
                "breath": 0}
_WORD_EDGES = " \t.,;:!?…\"'“”‘’«»()[]"


def _spoken_run(beat: Beat, text: str, max_words: int = 3) -> str | None:
    """As palavras faladas da batida que batem com `text` (sem acento nem pontuação), escritas como na fala. None:
    não foram ditas (ou passam de `max_words`)."""
    wanted = [t for t in (normalize_token(w) for w in text.split()) if t]
    if not wanted or len(wanted) > max_words:
        return None
    spoken = [normalize_token(w) for w in beat.words]
    for k in range(len(spoken) - len(wanted) + 1):
        if spoken[k:k + len(wanted)] == wanted:
            return " ".join(w.strip(_WORD_EDGES) for w in beat.words[k:k + len(wanted)]) or None
    return None


def _mark_from(data: dict[str, Any], beat: Beat, kind: str, is_hook: bool, humor: str) -> BeatMark | None:
    """Marcas da batida a partir dos campos achatados da resposta (`role`, `emphasis`, `serious`, `gag_*`). A ênfase
    só fica com palavras realmente faladas na batida; batida séria (ou estilo sem humor) não tem piada. None: a
    resposta não trouxe marca nenhuma (resposta antiga)."""
    role = pick(data.get("role"), _ROLES)
    emphasis: list[str] = []
    for raw in _list(data.get("emphasis")):
        word = _spoken_run(beat, _text(raw, 60)) if isinstance(raw, str) else None
        if word and word not in emphasis:
            emphasis.append(word)
        if len(emphasis) == 3:
            break
    serious = data.get("serious") is True
    gag = None
    payoff = pick(data.get("gag_payoff"), _PAYOFFS)
    if payoff and not serious and humor != "none":
        at = _spoken_run(beat, _text(data.get("gag_at"), 60), max_words=1) if data.get("gag_at") else None
        gag = GagHint(at=at if at and PRESENTER_AT_RE.match(at) else None, payoff=payoff,  # type: ignore[arg-type]
                      line=_words_max(data.get("gag_line"), 6))
    if role is None and not emphasis and not serious and gag is None:
        return None
    if is_hook:
        role = "hook"
    elif role in (None, "breath") and beat.role == "cta":
        role = "cta"
    elif role is None:
        role = "number" if kind in DATA_KINDS else "breath"
    energy = _ROLE_ENERGY.get(role, 1)
    return BeatMark(role=role, emphasis=emphasis, energy=min(energy, 1) if serious else energy,  # type: ignore[arg-type]
                    serious=serious, gag=gag)


# --- real primeiro: acervo, gancho, fundo dos cartões e teto de IA em tempo (ADR 0004) -------------

_SOURCE_OF_TIER = {"own": "upload", "capture": "capture", "stock": "pexels", "meme": "library"}


def usable_item(item: PoolItem) -> bool:
    """O item pode ser a imagem (ou o fundo) de uma batida: material real e não meme. Meme é reação à frase (entra
    como evento de piada, pelo editor), nunca decoração; `graphic` e `ai` do acervo não são material real."""
    return tiers.is_real(item.tier) and item.media != "meme"


def media_from_pool(item: PoolItem) -> MediaRef:
    """A mídia PENDENTE de anexo da cena que mostra um item do acervo (ADR 0004 §3.4/§3.5): nível (`tier`), `pool_id`,
    tipo, origem, crédito, licença, `rects` e duração, sem arquivo (`asset`/`video`) e sem ponto de entrada.

    Quem baixa/copia o item para a pasta do projeto e preenche `asset`, `video` e `start_s` é
    `media_pool.attach_chosen` (o proxy do clipe já é o trecho: `MediaRef.start_s` é o deslocamento dentro do `video`,
    não o `PoolItem.start_s` no vídeo de origem). Mesmo o item já local fica pendente: o arquivo do acervo vive no cache
    da biblioteca, fora do projeto. Enquanto pendente, o build usa o prompt de reserva da cena (com aviso)."""
    if item.media in ("clip", "capture", "meme"):
        kind = item.media
    else:
        kind = {"official": "official", "capture": "capture"}.get(item.tier, "photo")
    url = (item.url or "").lower()
    if item.tier == "own":
        source = "upload" if (item.path or "").startswith("uploads/") else "library"
    elif item.tier == "official":
        source = "youtube" if item.media == "clip" or "youtu" in url else "link"
    elif item.tier == "free":
        source = "commons" if "wikimedia" in url else "openverse" if "openverse" in url else "link"
    else:
        source = _SOURCE_OF_TIER.get(item.tier, "link")
    return MediaRef(
        kind=kind, source=source,  # type: ignore[arg-type]
        url=item.url, page_url=item.page_url, title=_text(item.desc, 200), license=item.license, credit=item.credit,
        editorial=item.editorial or item.tier == "official", dur_s=item.dur_s, rects=[list(r) for r in item.rects],
        tier=item.tier, pool_id=item.id,
    )


def _ai_language(art: ArtDirection, style: StyleSpec, policy: SourcePolicy) -> str:
    """A linguagem de TODAS as imagens de IA do storyboard (regra 8 do pedido): a dominante da direção (sem a foto, se
    a política não deixa a IA imitar foto, e sem maquete de interface), com ou sem acervo. A linguagem que a resposta
    pede por imagem não vale: cada imagem num estilo (vetor, retícula, papel recortado) denuncia o gerador."""
    base = list(art.languages) or list(style.art.languages[:3]) or ["editorial"]
    return house_language(base, photoreal=policy.ai_photo)


def _uses(picks: list[_Pick]) -> dict[str, int]:
    out: dict[str, int] = {}
    for p in picks:
        if p.source == "pool" and p.item is not None:
            out[p.item.id] = out.get(p.item.id, 0) + 1
    return out


def _pick_prov(picks: list[_Pick], i: int) -> str:
    """O nível do que a batida mostra (espelho de `sources.prov_of_scene` antes de existir a cena)."""
    seen: set[int] = set()
    while i not in seen:
        seen.add(i)
        p = picks[i]
        if p.source == "pool" and p.item is not None:
            return p.item.tier
        if p.source == "new":
            return "ai"
        if p.source == "reuse" and p.root is not None:
            i = p.root
            continue
        break
    return "graphic"


def _seconds_by_prov(picks: list[_Pick]) -> dict[str, float]:
    out: dict[str, float] = {}
    for i, p in enumerate(picks):
        prov = _pick_prov(picks, i)
        out[prov] = out.get(prov, 0.0) + p.beat.seconds
    return out


def _ai_window(picks: list[_Pick]) -> tuple[float, float, float]:
    """O trecho de `tiers.AI_WINDOW_S` com mais IA, pela duração estimada das batidas: `(fração, início, fim)`."""
    spans: list[tuple[float, float]] = []
    t = 0.0
    for i, p in enumerate(picks):
        if _pick_prov(picks, i) == "ai":
            spans.append((t, t + p.beat.seconds))
        t += p.beat.seconds
    share, start = tiers.window_share(spans, t)
    return share, start, start + min(tiers.AI_WINDOW_S, t)


def _official_room(picks: list[_Pick], policy: SourcePolicy, seconds: float) -> bool:
    total = sum(p.beat.seconds for p in picks) or 1.0
    return _seconds_by_prov(picks).get("official", 0.0) + seconds <= policy.official_max_share * total + 1e-6


def _specific_item(pool: Pool, topic: set[str], picks: list[_Pick], policy: SourcePolicy,
                   seconds: float = 0.0, *, official: bool = True, texts: tuple[str, ...] = ()) -> PoolItem | None:
    """O item real do acervo que fala do mesmo assunto da batida (pelo menos uma palavra em comum), o menos usado e o
    mais parecido; oficial só enquanto cabe no teto de material oficial (e nunca com `official=False`); foto de banco
    só quando tem a ver com a fala (`media_pool.fits_scene` com `texts`)."""
    uses = _uses(picks)
    order = {t: k for k, t in enumerate(_FALLBACK_TIERS)}
    best: tuple[Any, ...] | None = None
    chosen: PoolItem | None = None
    for index, item in enumerate(pool.items):
        if not usable_item(item):
            continue
        overlap = len(topic & _item_tokens(item))
        if overlap == 0:
            continue
        if item.tier == "official" and not (official and _official_room(picks, policy, seconds)):
            continue
        if not media_pool.fits_scene(item, *texts):
            continue
        key = (-overlap, uses.get(item.id, 0), order.get(item.tier, 99), -item.score, index)
        if best is None or key < best:
            best, chosen = key, item
    return chosen


def _fallback_item(pool: Pool, topic: set[str], picks: list[_Pick], policy: SourcePolicy,
                   texts: tuple[str, ...] = ()) -> PoolItem | None:
    """Reserva do `pool_id` inválido (ou de item que não serve de imagem): no mesmo tópico, o primeiro nível de
    `sources.FALLBACK` que o acervo tem; sem nada do tópico, o item real genérico menos usado, nunca foto de banco
    (banco aleatório é pior que o cartão). None: nenhum item real (vira cartão). Oficial só enquanto cabe no teto de
    material oficial; foto de banco só quando tem a ver com a fala (`media_pool.fits_scene` com `texts`)."""
    uses = _uses(picks)
    order = {t: k for k, t in enumerate(_FALLBACK_TIERS)}
    room = _official_room(picks, policy, 0.0)
    candidates = [(index, item) for index, item in enumerate(pool.items)
                  if item.tier in order and usable_item(item) and (room or item.tier != "official")]
    if not candidates:
        return None
    same = [(index, item) for index, item in candidates
            if topic & _item_tokens(item) and media_pool.fits_scene(item, *texts)]
    if same:
        return min(same, key=lambda c: (order[c[1].tier], -len(topic & _item_tokens(c[1])), uses.get(c[1].id, 0),
                                        -c[1].score, c[0]))[1]
    generic = [c for c in candidates if c[1].tier not in media_pool.KEYWORD_TIERS]
    if not generic:
        return None
    return min(generic, key=lambda c: (uses.get(c[1].id, 0), order[c[1].tier], -c[1].score, c[0]))[1]


def _clear_ai(p: _Pick) -> None:
    p.prompt, p.ai_reason, p.video = "", "", None
    p.language = p.shot = p.composition = p.focus = None
    p.entities = []


def _to_pool(p: _Pick, item: PoolItem) -> None:
    """A batida passa a mostrar o item do acervo. O prompt de IA que ela tinha fica de reserva (vale se a mídia não
    for anexada); o resto da imagem de IA sai."""
    reserve = p.prompt
    _clear_ai(p)
    p.prompt = reserve
    p.source, p.root, p.item, p.real = "pool", None, item, None


def _to_graphic(p: _Pick) -> None:
    """A batida vira cartão de texto (a fala grande) sobre o real vizinho (`_real_backgrounds`)."""
    _clear_ai(p)
    if p.kind == "image":
        p.kind, p.overlay = "text", None
    p.source, p.root, p.item, p.real, p.motion, p.plain = "none", None, None, None, None, False


def _item_text(item: PoolItem) -> str:
    """O item do acervo para o usuário: a descrição curta e o tipo de material (nunca o id nem o código do nível)."""
    label = storyboard_check.PROV_LABELS.get(item.tier, item.tier)
    desc = _text(item.desc, 60)
    return f'"{desc}" ({label})' if desc else label


def _real_hook(picks: list[_Pick], pool: Pool, policy: SourcePolicy, hook: int | None, lang: str) -> list[Issue]:
    """O gancho é o melhor item do acervo nos níveis do gancho da política (próprio, oficial, captura, foto livre):
    o do assunto da 1ª batida e, no empate, o primeiro nível e a maior nota. Sem item desses níveis, o melhor real
    (a revisão acusa `hook_not_real`). O contador do Top 10 fica como é."""
    if hook is None or picks[hook].kind == "count":
        return []
    p = picks[hook]
    if p.source == "pool" and p.item is not None and p.item.tier in policy.hook:
        return []
    candidates = [it for it in pool.items if it.tier in policy.hook and usable_item(it)
                  and media_pool.fits_scene(it, *p.texts)] or [
        it for it in pool.items if usable_item(it) and media_pool.fits_scene(it, *p.texts)]
    if not candidates:
        return []
    rank = {t: k for k, t in enumerate(policy.hook)}
    best = min(enumerate(candidates), key=lambda c: (-len(p.topic & _item_tokens(c[1])), rank.get(c[1].tier, 99),
                                                     -c[1].score, c[0]))[1]
    if p.kind in CARD_KINDS:
        # Como `storyboard_check.fix_hook`: o número ou o texto do cartão vira a faixa do gancho.
        card = p.card or SceneCard()
        if not card.headline.get(lang):
            carried = card.value.get(lang) or p.overlay or ""
            if carried and card.label.get(lang):
                carried = f"{carried} {card.label[lang]}"
            if carried:
                card.headline = {lang: " ".join(carried.split()[:5])}
        card.value, card.label, card.items, card.highlight = {}, {}, [], None
        p.card = card if card.resolved(lang) else None
        p.kind, p.overlay, p.number = "image", None, None
    _to_pool(p, best)
    p.motion = None  # o gancho ganha o punch_zoom
    return [Issue("gancho_real", CORRIGIDO, f"O gancho mostra o material real do acervo: {_item_text(best)}.",
                  p.beat.key)]


def _require_reason(picks: list[_Pick], pool: Pool, policy: SourcePolicy, hook: int | None) -> list[Issue]:
    """Imagem de IA sem motivo declarado, havendo no acervo um item real do mesmo assunto: usa o item."""
    if not policy.require_ai_reason:
        return []
    issues: list[Issue] = []
    for i, p in enumerate(picks):
        if p.source != "new" or p.ai_reason or i == hook or p.kind == "count":
            continue
        item = _specific_item(pool, p.topic, picks, policy, p.beat.seconds, texts=p.texts)
        if item is None:
            continue
        _to_pool(p, item)
        issues.append(Issue("ia_sem_motivo", CORRIGIDO, "A cena pedia imagem de IA sem motivo: mostra o item do acervo "
                                                        f"{_item_text(item)}.", p.beat.key))
    return issues


def _real_backgrounds(picks: list[_Pick], policy: SourcePolicy) -> None:
    """Fundo dos cartões com acervo: o item real mais perto (antes, no empate); o fundo liso pedido fica liso; fundo
    de IA só quando não há real nenhum. Material oficial só enquanto cabe no teto (`official_max_share`): passou dele,
    o real não oficial mais perto ou, sem ele, o fundo liso. O contador do Top 10 e o mapa ficam como são."""
    owners = [k for k, q in enumerate(picks) if q.source == "pool" and q.item is not None and usable_item(q.item)]
    for i, p in enumerate(picks):
        if p.kind not in CARD_KINDS or p.kind in ("count", "map") or p.plain or p.source == "pool":
            continue
        if p.source == "reuse" and p.root is not None and _pick_prov(picks, i) != "ai":
            continue
        room = _official_room(picks, policy, p.beat.seconds)
        ranked = sorted((k for k in owners if k != i), key=lambda k: (abs(k - i), k > i))
        near = next((k for k in ranked if room or _pick_prov(picks, k) != "official"), None)
        if near is not None:
            p.source, p.root = "reuse", near
        elif ranked:
            p.source, p.root = "none", None  # só havia real oficial e o teto acabou: fundo liso, nunca IA
        elif p.source == "none":
            last = next((k for k in range(i - 1, -1, -1) if picks[k].source == "new"), None)
            if last is not None:
                p.source, p.root = "reuse", last


def _flatten_roots(picks: list[_Pick]) -> None:
    """Quem aponta para uma batida que virou reaproveitamento passa a apontar para a dona da imagem."""
    for p in picks:
        hops = 0
        while p.source == "reuse" and p.root is not None and picks[p.root].source == "reuse" and hops < len(picks):
            p.root = picks[p.root].root
            hops += 1
        if p.source == "reuse" and p.root is not None and picks[p.root].source == "none":
            # A dona virou cartão liso (não há real para o fundo): sem imagem para mostrar.
            if p.kind == "image":
                _to_graphic(p)
            else:
                p.source, p.root = "none", None


def _enforce_ai_time(picks: list[_Pick], pool: Pool, policy: SourcePolicy, hook: int | None, budget: int,
                     positions: list[int]) -> list[Issue]:
    """Teto de IA em TEMPO de tela (`ai_max_share` da duração estimada das batidas na média do vídeo, e
    `tiers.ai_window_cap` em qualquer trecho de `tiers.AI_WINDOW_S`) e em número de imagens novas (`budget`). O
    excesso vira o item real do mesmo assunto ou, sem ele, cartão sobre o real vizinho; nunca o reaproveitamento da
    mesma imagem de IA. Sai primeiro o reaproveitamento de IA, depois a IA sem motivo e por último a IA com motivo
    (das últimas batidas para as primeiras; quando só o trecho passa do teto, só as batidas dele)."""
    total = sum(p.beat.seconds for p in picks)
    cap = policy.ai_max_share * total

    def over_total() -> bool:
        ai = _seconds_by_prov(picks).get("ai", 0.0)
        news = sum(1 for p in picks if p.source == "new")
        return ai > cap + 1e-6 or news > budget

    window_cap = tiers.ai_window_cap(policy.ai_max_share)

    def over() -> bool:
        return over_total() or _ai_window(picks)[0] > window_cap + 1e-6

    def candidates() -> list[int]:
        reused = [i for i, p in enumerate(picks) if p.source == "reuse" and p.kind == "image"
                  and _pick_prov(picks, i) == "ai"]
        bare = [i for i, p in enumerate(picks) if p.source == "new" and not p.ai_reason and i != hook]
        reasoned = [i for i, p in enumerate(picks) if p.source == "new" and p.ai_reason and i != hook]
        news = sum(1 for p in picks if p.source == "new")
        # Acima do número de imagens novas, o reaproveitamento não resolve: começa pelas imagens novas.
        first = [] if news > budget else reused[::-1]
        order = [*first, *bare[::-1], *reasoned[::-1], *([] if first else reused[::-1])]
        if over_total():
            return order
        # Só o trecho passa do teto: saem as batidas de IA que caem nele.
        _share, lo, hi = _ai_window(picks)
        starts = [sum(q.beat.seconds for q in picks[:k]) for k in range(len(picks))]
        return [i for i in order if starts[i] < hi - 1e-6 and starts[i] + picks[i].beat.seconds > lo + 1e-6]

    issues: list[Issue] = []
    tried: set[int] = set()
    while over():
        todo = [i for i in candidates() if i not in tried]
        if not todo:
            break
        windowed = not over_total()
        i = todo[0]
        tried.add(i)
        p = picks[i]
        item = _specific_item(pool, p.topic, picks, policy, p.beat.seconds, texts=p.texts)
        if item is not None:
            _to_pool(p, item)
            what = f"mostra o item do acervo {_item_text(item)}"
        else:
            _to_graphic(p)
            what = "virou cartão sobre o material real vizinho"
        where = (f"{round(window_cap * 100)}% num trecho de {tiers.AI_WINDOW_S:.0f} s" if windowed else
                 f"{round(policy.ai_max_share * 100)}% do tempo")
        issues.append(Issue("teto_de_ia", CORRIGIDO, f"Passou do teto de IA ({where}): a cena {positions[i]} {what}.",
                            p.beat.key))
        _real_backgrounds(picks, policy)
        _flatten_roots(picks)
    return issues


def _enforce_official(picks: list[_Pick], pool: Pool, policy: SourcePolicy, hook: int | None,
                      positions: list[int]) -> list[Issue]:
    """Teto de material oficial em tempo de tela (`official_max_share`, risco de Content ID), como o de IA: das
    últimas batidas para as primeiras (o gancho fica), a escolha oficial vira um item real não oficial do mesmo assunto
    ou, sem ele, cartão (o fundo do cartão sai de um real não oficial ou fica liso, `_real_backgrounds`)."""
    total = sum(p.beat.seconds for p in picks) or 1.0
    cap = policy.official_max_share * total

    def over() -> bool:
        return _seconds_by_prov(picks).get("official", 0.0) > cap + 1e-6

    issues: list[Issue] = []
    tried: set[int] = set()
    while over():
        todo = [i for i in range(len(picks) - 1, -1, -1)
                if i not in tried and i != hook and _pick_prov(picks, i) == "official"]
        if not todo:
            break
        i = todo[0]
        tried.add(i)
        p = picks[i]
        item = _specific_item(pool, p.topic, picks, policy, p.beat.seconds, official=False, texts=p.texts)
        if p.kind in CARD_KINDS:
            # Cartão sobre o oficial: o fundo passa a ser outro real (ou liso).
            if item is not None:
                p.source, p.root, p.item = "pool", None, item
                what = f"passou a usar de fundo {_item_text(item)}"
            else:
                p.source, p.root, p.item, p.plain = "none", None, None, True
                what = "ficou com o fundo liso"
        elif item is not None:
            _to_pool(p, item)
            what = f"mostra {_item_text(item)}"
        else:
            _to_graphic(p)
            what = "virou cartão"
        issues.append(Issue("teto_oficial", CORRIGIDO, "Passou do teto de material oficial "
                                                       f"({round(policy.official_max_share * 100)}% do tempo): a cena "
                                                       f"{positions[i]} {what}.", p.beat.key))
        _real_backgrounds(picks, policy)
        _flatten_roots(picks)
    return issues


def _real_first(picks: list[_Pick], pool: Pool, policy: SourcePolicy, hook: int | None, budget: int,
                positions: list[int], lang: str) -> list[Issue]:
    """Storyboard com acervo: gancho real, IA só com motivo, cartões sobre o real e os tetos de IA e de material
    oficial em tempo de tela."""
    issues = _real_hook(picks, pool, policy, hook, lang)
    issues += _require_reason(picks, pool, policy, hook)
    _real_backgrounds(picks, policy)
    _flatten_roots(picks)
    issues += _enforce_ai_time(picks, pool, policy, hook, budget, positions)
    issues += _enforce_official(picks, pool, policy, hook, positions)
    _real_backgrounds(picks, policy)
    _flatten_roots(picks)
    return issues


def _scenes_from(doc: ProjectDoc, style: StyleSpec, art: ArtDirection, beats: list[Beat], raw_scenes: object,
                 budget: int, positions: list[int], pool: Pool | None = None) -> tuple[list[Scene], list[Issue]]:
    """Cenas das batidas a partir da resposta (determinístico; nada aqui chama serviço externo).

    Com acervo de material real (`pool` com item real), o real vem primeiro (ADR 0004): `source "pool"` com `pool_id`
    válido vira `Visual.media` pendente de anexo (`media_from_pool`, com prompt de IA de reserva); id inválido, ou
    item que não serve de imagem (meme, gráfico, IA), segue `sources.FALLBACK` no mesmo tópico; o gancho é o melhor
    item do acervo; o fundo do cartão é o real vizinho; e os tetos de IA e de material oficial são em tempo de tela
    (`_real_first`). Sem acervo, o de sempre (a linguagem da IA segue a política de fontes)."""
    lang = doc.primary_language
    real_first = has_real(pool)
    policy = sources_for(style)
    grammar = grammar_for(style)
    ai_language = _ai_language(art, style, policy)
    by_key: dict[str, dict[str, Any]] = {}
    for item in _list(raw_scenes):
        if isinstance(item, dict) and isinstance(item.get("key"), str):
            by_key.setdefault(item["key"].strip(), item)
    is_top = style.structure == Structure.top10
    safe = compositions_for_caption(style.edit.captions.position)
    entity_ids = {e.id for e in art.entities}
    index_of = {beat.key: i for i, beat in enumerate(beats)}
    # O gancho (1ª cena do vídeo) tem prioridade no orçamento; o contador do Top 10 fica como é.
    hook = 0 if beats and storyboard_check.is_hook_beat(doc, beats[0]) else None
    data_style = is_data_style(style)

    picks: list[_Pick] = []
    early: list[Issue] = []
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
        kind, card = _card_from(data, kind, lang, doc.sources, data_style=data_style)
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
        topic = _match_tokens(beat.text, data.get("prompt"), data.get("real_query"), data.get("real_note"),
                              data.get("headline"), data.get("overlay_text"))
        texts = tuple(t for t in (beat.text, data.get("headline"), data.get("overlay_text"), data.get("prompt"),
                                  data.get("real_query")) if isinstance(t, str) and t.strip())
        chosen_item: PoolItem | None = None
        plain = False
        if real_first and kind not in ("count", "map"):
            assert pool is not None
            if source == "pool":
                cited = pool.item(_text(data.get("pool_id"), 12))
                fits = cited is not None and media_pool.fits_scene(cited, *texts)
                chosen_item = cited if cited is not None and usable_item(cited) and fits else None
                if chosen_item is None:
                    # id inválido (ou ausente), item que não serve de imagem da batida (meme é reação, nunca
                    # decoração; imagem de IA e gráfico do acervo não são material real) ou foto de banco que não tem
                    # a ver com a fala (robô para "golpe", rolo de filme para "ISO"): a reserva no mesmo tópico.
                    chosen_item = _fallback_item(pool, topic, picks, policy, texts)
                    why = ("A cena citava um item que não está no acervo" if cited is None else
                           f"A cena citava {_item_text(cited)}, que não tem a ver com a fala" if not fits else
                           f"A cena citava {_item_text(cited)}, que não serve de imagem da batida")
                    early.append(Issue("acervo_reserva", CORRIGIDO, f"{why}: " + (
                        f"usa {_item_text(chosen_item)}." if chosen_item else "virou cartão."), beat.key))
                if chosen_item is None:
                    source = "none"
                    kind = "text" if kind == "image" else kind
            elif source == "none" and kind in CARD_KINDS:
                plain = True  # o LLM pediu o fundo liso: fica liso
        j = index_of.get(_text(data.get("reuse_key"), 120)) if source == "reuse" else None
        if j is not None and j < i:
            root = j if picks[j].source in ("new", "pool") else picks[j].root
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
        if chosen_item is not None:
            source, root = "pool", None  # item do acervo: imagem ou fundo do cartão
        elif real_first and kind in CARD_KINDS and kind not in ("count", "map") and root is None:
            source = "none"  # fundo do cartão: o real vizinho, decidido depois de todas as batidas (`_real_backgrounds`)
        elif kind == "image":
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
                  real=_real_from(data, kind, doc.sources) if chosen_item is None else None, item=chosen_item,
                  topic=topic, texts=texts, plain=plain, mark=_mark_from(data, beat, kind, i == hook, grammar.humor))
        if p.source == "pool":
            p.prompt = _text(data.get("prompt"), 700)  # reserva de IA, quando a resposta trouxe
        if p.source == "new":
            p.prompt = _text(data.get("prompt"), 700) or beat.text
            p.ai_reason = _text(data.get("ai_reason"), 200)
            p.language = ai_language  # um estilo só para todas as imagens de IA (regra 8), com ou sem acervo
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
            p.video = _text(data.get("video_motion"), 300) or None if p.kind == "image" else None
        elif p.source == "reuse":
            p.focus = pick(data.get("focus"), FOCUS_POINTS)
        p.motion = pick(data.get("motion"), _MOTIONS)
        picks.append(p)

    if real_first:
        assert pool is not None
        issues = early + _real_first(picks, pool, policy, hook, budget, positions, lang)
    else:
        issues = _enforce_budget(picks, budget, hook, positions)
    _resolve_ahead(picks)
    issues += _trim_headlines(picks, lang, hook)
    issues += _limit_real(picks, policy.ai_max_share)
    _limit_video(picks, video_limit(style))

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
                            shot=p.shot, composition=p.composition, entities=p.entities,
                            ai_reason=p.ai_reason or None)
        elif p.source == "pool" and p.item is not None:
            # Mídia do acervo pendente de anexo (`media_pool.attach_chosen` baixa e preenche o arquivo). A cena de
            # imagem guarda um prompt de IA de reserva (o da resposta ou a fala), como na mídia anexada à mão: vale
            # quando a mídia sai ou não é anexada (o build avisa), e a cena nunca fica vazia.
            reserve = (p.prompt or beat.text) if p.kind == "image" else p.prompt
            visual = Visual(prompt=reserve, seed=seed_from(doc.seed, beat.segment_id, beat.part),
                            language=ai_language if reserve else None, media=media_from_pool(p.item))
        elif p.source == "reuse":
            visual = Visual()
        else:
            visual = Visual(type="none")
        scenes.append(Scene(segment_id=beat.segment_id, part=beat.part, parts=beat.parts, kind=p.kind,  # type: ignore[arg-type]
                            visual=visual, overlay_text={lang: p.overlay} if p.overlay else {}, number=p.number,
                            motion=p.motion, span=len(beat.words), card=p.card, focus=p.focus, real=p.real,
                            video_hint=p.video if p.source == "new" else None, mark=p.mark))
    for p, scene in zip(picks, scenes, strict=True):
        if p.source == "reuse" and p.root is not None:
            scene.visual.reuse_of = scenes[p.root].id
    id_of = {p.beat.key: scene.id for p, scene in zip(picks, scenes, strict=True)}
    for issue in issues:
        issue.scene_id = id_of.get(issue.scene_key or "")
    return scenes, issues


def video_limit(style: StyleSpec) -> int:
    """Quantas sugestões de vídeo IA o storyboard pode fazer (o mesmo limite de cenas marcadas)."""
    from studio.workers.pipeline.video_clips import max_video_scenes

    return max_video_scenes(style)


def _limit_video(picks: list[_Pick], limit: int) -> None:
    """Fica com as primeiras `limit` sugestões de vídeo IA (o gancho vem primeiro); as outras saem sem aviso: são só
    sugestões e o usuário escolhe na aba Cenas."""
    hinted = [p for p in picks if p.video]
    for p in hinted[max(0, limit):]:
        p.video = None


def _resolve_ahead(picks: list[_Pick]) -> None:
    """Contador do Top 10: mostra a imagem de uma batida seguinte do mesmo item (a indicada pelo LLM ou a
    primeira com imagem); sem nenhuma, fica com o fundo animado."""
    for i, p in enumerate(picks):
        if p.ahead is None:
            continue
        same = [k for k in range(i + 1, len(picks)) if picks[k].beat.segment_id == p.beat.segment_id]
        order = ([p.ahead] if p.ahead in same else []) + [k for k in same if k != p.ahead]
        for k in order:
            owner = ("new", "pool")  # imagem nova ou item do acervo
            target = k if picks[k].source in owner else picks[k].root if picks[k].source == "reuse" else None
            if target is not None and picks[target].source in owner:
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
    (mesmo segmento, parte, número de partes e palavras) recebe uma cópia dela, mesmo sobre a escolha nova do acervo
    (a escolha à mão vale mais). A mídia que o storyboard anterior escolheu sozinho do acervo (`pool_id`) não passa:
    o storyboard novo escolhe de novo (o `pool_id` antigo pode nem existir no acervo recoletado). Devolve quantas
    cenas."""
    attached = {(s.segment_id, s.part, s.parts, s.span): s.visual.media for s in old.scenes
                if s.visual.media is not None and not s.visual.media.pool_id and s.segment_id not in keep_segments}
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
             budget: int, style_ids: set[str], characters: list[Character] | None = None,
             pool: Pool | None = None) -> _Attempt:
    """Documento novo a partir de uma resposta do LLM, já normalizado e revisado."""
    payload = data if isinstance(data, dict) else {}
    if doc.art is not None and doc.art.source == "user":
        art = doc.art.model_copy(deep=True)
    else:
        art = _direction_from(payload.get("direction"), style, current_style_id=doc.style_id, style_ids=style_ids)
    # Personagens cadastrados viram entidades antes das cenas (as cenas só aceitam ids de entidades da direção).
    art, remap = character_scenes.prepare(art, characters or [])
    new_doc = doc.model_copy(deep=True)
    kept = [s for s in new_doc.scenes if s.segment_id in keep_segments]
    _detach_kept(doc, kept)
    scenes, issues = _scenes_from(new_doc, style, art, beats, payload.get("scenes"), budget,
                                  _positions(doc, beats, keep_segments), pool)
    new_doc.scenes = kept + scenes
    carried = _carry_media(doc, new_doc, keep_segments)
    if carried:
        text = "1 cena manteve" if carried == 1 else f"{carried} cenas mantiveram"
        issues.append(Issue("midia_mantida", CORRIGIDO, f"{text} a mídia real que você anexou."))
    new_doc.art = art
    new_doc.storyboard_approved = False
    new_doc = ProjectDoc.model_validate(new_doc.model_dump())
    issues += character_scenes.finish(new_doc, beats, characters or [], remap)
    issues += storyboard_check.check(new_doc, style, beats, budget, pool=pool)
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


def _review(doc: ProjectDoc, budget: int, issues: list[Issue], retried: bool, *, style: StyleSpec | None = None,
            pool: Pool | None = None) -> dict[str, Any]:
    """Resumo da curadoria (vai para `settings.storyboard_review`, sem provider e version). Com acervo, as contagens
    ganham a parte de IA e de real no tempo de tela."""
    art = doc.art
    return {
        "counts": storyboard_check.review_counts(doc, budget, style=style, pool=pool),
        "issues": [i.as_dict() for i in storyboard_check.sort_issues(issues)],
        "retried": retried,
        "style_fit": art.style_fit if art else "ok",
        "style_note": art.style_note if art else "",
        "suggested_style": art.suggested_style if art else None,
    }


def review_attached(doc: ProjectDoc, style: StyleSpec, review: dict[str, Any], pool: Pool | None) -> dict[str, Any]:
    """A revisão do storyboard depois do anexo do acervo (`media_pool.attach_chosen`), sobre o documento que vai ser
    gravado: as contagens de novo (a mídia que chegou conta como real; a que caiu na reserva, como IA) e um aviso por
    cena cuja mídia do acervo ficou pendente (`midia_pendente`). Sem acervo real e sem pendência, fica como veio."""
    counts = review.get("counts") or {}
    pending = storyboard_check.pending_media_issues(doc)
    if not pending and not has_real(pool) and not counts.get("media_pending"):
        return review
    fresh = storyboard_check.review_counts(doc, int(counts.get("budget", 0)), style=style, pool=pool)
    order = {storyboard_check.GRAVE: 0, storyboard_check.AVISO: 1, CORRIGIDO: 2}
    issues = [i for i in review.get("issues") or [] if i.get("code") != "midia_pendente"]
    issues = sorted([*issues, *(i.as_dict() for i in pending)], key=lambda i: order.get(i.get("severity"), 9))
    return {**review, "counts": fresh, "issues": issues}


def storyboard(doc: ProjectDoc, style: StyleSpec, *, project_id: str | None = None, job_id: str | None = None,
               channel: dict[str, Any] | None = None, styles: dict[str, str] | None = None,
               characters: list[Character] | None = None,
               pool: Pool | None = None) -> tuple[ProjectDoc, str, dict[str, Any]]:
    """Storyboard v2: direção de arte do vídeo e uma cena por batida, revisadas antes de gastar imagens.

    Segmentos com cena travada ficam como estão. Se a revisão achar problema grave, pede uma correção ao
    LLM (uma vez só) e fica a resposta com menos problemas graves. Devolve (documento, provedor, revisão).

    `pool`: o acervo de material real (ADR 0004); None lê o `pool.json` do projeto (gravado pela coleta antes do
    storyboard). Com material real, o real vem primeiro; sem ele, o storyboard de sempre.
    """
    if pool is None:
        try:
            pool = load_pool(project_id) if project_id else Pool()
        except ValueError:  # id de projeto fora do padrão: sem acervo, o storyboard de sempre
            pool = Pool()
    keep_segments = {s.segment_id for s in doc.scenes if s.locked}
    beats = plan_beats(doc, style, keep_segments)
    budget = image_budget(len(beats), style, pool)
    if not beats:
        new_doc = doc.model_copy(deep=True)
        new_doc.storyboard_approved = False
        new_doc = ProjectDoc.model_validate(new_doc.model_dump())
        return new_doc, "nenhum", _review(new_doc, budget, [], retried=False, style=style, pool=pool)
    catalog = styles if styles is not None else style_catalog(style)
    if characters is None:
        characters = characters_mod.for_doc(doc, (channel or {}).get("style_bible") or {})
    prompt = storyboard_prompt(doc, style, beats, budget, channel=channel, styles=catalog, characters=characters,
                               pool=pool)
    # Avatar do apresentador (só com o avatar efetivo no vídeo): as poses entram no pedido e as cenas ganham os campos
    # achatados da dica (pose, palavra, modo).
    presenter = _storyboard_presenter(doc, style, channel)
    schema = storyboard_schema(presenter is not None)
    if presenter is not None:
        prompt += "\n" + "\n".join([*presenter_scenes.prompt_lines(presenter), PRESENTER_FIELDS_LINE])
    context = _storyboard_context(doc, style, beats, budget, pool)
    result, provider_id = _llm("storyboard", STORYBOARD_SYSTEM, prompt, schema, context, project_id, job_id)
    best = _attempt(doc, style, beats, result.data, keep_segments, budget, set(catalog), characters, pool)
    best_data: object = result.data
    severe = best.graves
    retried = False
    if severe:
        retry_context = {**context, "retry": True, "issues": [i.as_dict() for i in severe]}
        try:
            second, second_provider = _llm("storyboard", STORYBOARD_SYSTEM, prompt + _correction_block(result.data, severe),
                                           schema, retry_context, project_id, job_id)
        except Exception as exc:  # noqa: BLE001 - a primeira resposta continua valendo
            log.warning("a correção do storyboard falhou; fica a primeira resposta: %s", exc)
        else:
            retried = True
            other = _attempt(doc, style, beats, second.data, keep_segments, budget, set(catalog), characters, pool)
            if len(other.graves) < len(severe):
                best, provider_id, best_data = other, second_provider, second.data
    issues = best.issues
    swapped = _swap_invented_cards(best.doc, issues)
    if swapped:
        issues = [i for i in issues if not (i.code == "numero_inventado" and i.severity == storyboard_check.GRAVE)]
        issues += swapped
    if presenter is not None:
        issues += _apply_presenter_hints(best.doc, style, presenter, best_data)
    return best.doc, provider_id, _review(best.doc, budget, issues, retried, style=style, pool=pool)


PRESENTER_FIELDS_LINE = ('Campos da dica em cada cena: presenter_pose (id da pose), presenter_at (a palavra) e '
                         'presenter_mode ("reacao" ou "explica"); null nos três quando a cena não tem avatar.')


def storyboard_schema(with_presenter: bool) -> dict[str, Any]:
    """O esquema do storyboard; com o avatar, as cenas ganham os campos achatados da dica (`PRESENTER_HINT_FIELDS`).
    Sem avatar, o esquema é exatamente o de antes (a saída de quem não usa não muda)."""
    if not with_presenter:
        return STORYBOARD_SCHEMA
    schema = json.loads(json.dumps(STORYBOARD_SCHEMA))
    item = schema["properties"]["scenes"]["items"]
    item["properties"].update(json.loads(json.dumps(PRESENTER_HINT_FIELDS)))
    item["required"] = list(item["properties"])
    return schema


def _storyboard_presenter(doc: ProjectDoc, style: StyleSpec, channel: dict[str, Any] | None) -> Presenter | None:
    """O avatar efetivo nas cenas deste projeto (canal, projeto e estilo), ou None."""
    from studio.core.project import presenter as presenter_core

    if not doc.channel_id:
        return None
    try:
        bible = (channel or {}).get("style_bible")
        source = bible if isinstance(bible, dict) else presenter_core.channel_presenter(doc.channel_id)
        return presenter_core.effective(source, doc, style, purpose="video")
    except Exception as exc:  # noqa: BLE001 - sem avatar o storyboard sai como antes
        log.warning("avatar do apresentador indisponível no storyboard: %s", exc)
        return None


def _apply_presenter_hints(doc: ProjectDoc, style: StyleSpec, presenter: Presenter, data: object) -> list[Issue]:
    """Converte as dicas do LLM (`presenter_pose/at/mode` por cena) em `Scene.presenter` com `source="auto"` (a
    distribuição trata como sugestão) e roda a distribuição com os tempos estimados pela velocidade da fala: as regras
    (espaçamento, cena séria, CTA feliz, abertura) já valem na tela antes da narração. O build distribui de novo com
    os tempos reais."""
    from studio.core.project import presenter as presenter_core

    by_key: dict[str, dict[str, Any]] = {}
    for item in (data.get("scenes") if isinstance(data, dict) else None) or []:
        if isinstance(item, dict) and isinstance(item.get("key"), str):
            by_key[item["key"]] = item
    for scene in doc.scenes:
        if scene.presenter is not None and scene.presenter.source == "manual":
            continue
        hint = by_key.get(f"{scene.segment_id}:{scene.part}") or {}
        pose = presenter_core.resolve_pose(presenter, _text(hint.get("presenter_pose"), 60)) if (
            hint.get("presenter_pose")) else None
        if pose is None:
            continue
        mode = hint.get("presenter_mode") if hint.get("presenter_mode") in ("reacao", "explica") else "reacao"
        try:
            scene.presenter = ScenePresenter(pose=pose.id, at=_text(hint.get("presenter_at"), 60) or None, mode=mode,
                                             source="auto")
        except ValueError:
            scene.presenter = ScenePresenter(pose=pose.id, mode=mode, source="auto")
    lang = doc.primary_language
    wpm = style.script.words_per_minute.get(lang, 150)
    vertical = style.format.height > style.format.width
    try:
        return presenter_scenes.assign(doc, presenter, presenter_scenes.estimate_words(doc, lang, wpm),
                                       vertical=vertical)
    except Exception as exc:  # noqa: BLE001 - a distribuição roda de novo no build
        log.warning("a distribuição do avatar falhou no storyboard: %s", exc)
        return []


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


def _style_or_none(doc: ProjectDoc) -> StyleSpec | None:
    """Estilo do projeto, ou None se ele não existe mais (a tradução segue sem orçamento de duração)."""
    try:
        return get_style(doc.style_id, doc.style_overrides)
    except StyleNotFound:
        return None


def _segment_line(item: dict[str, str]) -> str:
    budget = f" [{item['budget']}]" if item.get("budget") else ""
    return f"- {item['id']}{budget}: {item['text']}"


SHORTEN_SYSTEM = """Você adapta traduções de narração para dublagem. Cada texto precisa caber no tempo indicado: reescreva
mais curto, com o mesmo sentido e tom, sem cortar informação importante. Mantenha os IDs exatamente iguais."""


def _shorten_over_budget(doc: ProjectDoc, style: StyleSpec, lang: str, budgets: dict[str, dubbing.Budget],
                         project_id: str | None, job_id: str | None) -> list[str]:
    """Uma rodada pedindo versões mais curtas dos segmentos que passaram do orçamento (Modo B). Só troca o texto
    quando a versão nova é mais curta de fato; trecho travado fica como está. Devolve os ids encurtados; altera
    `doc` no lugar."""
    over = [sid for sid in dubbing.over_budget(doc, style, lang, skip_locked=True) if sid in budgets]
    if not over:
        return []
    items = [{"id": sid, "text": doc.segment(sid).text_for(lang), "budget": dubbing.budget_line(budgets[sid])}
             for sid in over]
    prompt = (f"Idioma: {lang_name(lang)}\nEstas traduções ficaram longas demais para o tempo do vídeo. Reescreva cada "
              "uma dentro do limite:\n" + "\n".join(_segment_line(item) for item in items))
    context = {"target_lang": lang, "title": doc.title, "segments": items, "overlays": [], "shorten": True}
    try:
        result, _ = _llm("translate", SHORTEN_SYSTEM, prompt, TRANSLATE_SCHEMA, context, project_id, job_id)
    except Exception as exc:  # noqa: BLE001 - a tradução longa ainda serve (o build acelera ou avisa)
        log.warning("não deu para encurtar a tradução em %s: %s", lang, exc)
        return []
    shortened = []
    for item in result.data.get("segments", []):
        sid, text = item.get("id"), (item.get("text") or "").strip()
        if sid not in over or not text:
            continue
        seg = doc.segment(sid)
        if dubbing.speech_seconds(text, lang, style) < dubbing.speech_seconds(seg.text_for(lang), lang, style):
            seg.text[lang] = text
            shortened.append(sid)
    return shortened


def shorten_translation(doc: ProjectDoc, lang: str, *, project_id: str | None = None,
                        job_id: str | None = None) -> tuple[ProjectDoc, list[str]]:
    """Modo B: encurta só os trechos de `lang` que passam do tempo do original, sem traduzir de novo (as correções
    manuais e os trechos travados ficam). Serve para quem passou ao Modo B depois da tradução. Fora do Modo B,
    não faz nada. Devolve o documento novo e os ids encurtados."""
    style = _style_or_none(doc)
    if style is None or not dubbing.is_multi_track(style, doc) or lang == doc.primary_language:
        return doc, []
    new_doc = doc.model_copy(deep=True)
    budgets = {b.segment_id: b for b in dubbing.segment_budgets(new_doc, style, lang)}
    shortened = _shorten_over_budget(new_doc, style, lang, budgets, project_id, job_id)
    if not shortened:
        return doc, []
    return ProjectDoc.model_validate(new_doc.model_dump()), shortened
def translation_pairs(doc: ProjectDoc, lang: str) -> list[tuple[str, str, str, str]]:
    """(tipo, id, texto original, texto em `lang`) da narração, das sobreposições e dos cartões já traduzidos.
    Tipo "segment" (id do segmento) ou "scene" (id da cena)."""
    source = doc.primary_language
    out: list[tuple[str, str, str, str]] = []
    for seg in doc.segments:
        original, translated = seg.text_for(source), seg.text.get(lang, "")
        if original.strip() and translated.strip():
            out.append(("segment", seg.id, original, translated))
    for scene in doc.scenes:
        original, translated = scene.overlay_text.get(source, ""), scene.overlay_text.get(lang, "")
        if original.strip() and translated.strip():
            out.append(("scene", scene.id, original, translated))
        for entry in _card_texts(scene, source):
            target = _card_target(scene, entry["id"].split("#", 1)[1])
            translated = (target or {}).get(lang, "")
            if translated.strip():
                out.append(("scene", scene.id, entry["text"], translated))
    return out


def glossary_issues(doc: ProjectDoc, entries: list[GlossaryEntry], langs: list[str] | None = None) -> list[dict[str, str]]:
    """Termos do glossário do canal que a tradução não respeitou, em todos os idiomas traduzidos (ou em `langs`)."""
    if not entries:
        return []
    targets = [lang for lang in (langs or doc.languages) if lang != doc.primary_language]
    return [issue for lang in targets for issue in check_pairs(translation_pairs(doc, lang), entries, lang)]


def translate(doc: ProjectDoc, target_lang: str, *, screen_only: bool = False, project_id: str | None = None,
              job_id: str | None = None, glossary: list[GlossaryEntry] | None = None) -> tuple[ProjectDoc, str]:
    """Traduz a narração e os textos de tela. Com `screen_only`, só os textos de tela que faltam em
    `target_lang`: a narração já traduzida (e a voz gravada dela) fica como está. O `glossary` do canal entra no
    pedido (só os termos que aparecem nos textos); a conferência fica em `glossary_issues`."""
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
    # Modo B (README §7.3): a faixa traduzida tem o tempo da imagem, então cada segmento leva o orçamento de duração
    # (o texto traduzido cabe no tempo do original). No Modo A cada idioma tem o vídeo próprio: sem limite.
    style = _style_or_none(doc)
    multi = (style is not None and style.languages.delivery == dubbing.MULTI_TRACK
             and len({*doc.languages, target_lang}) > 1)
    budgets = {b.segment_id: b for b in dubbing.segment_budgets(doc, style, target_lang)} if multi and segments else {}
    for item in segments:
        if item["id"] in budgets:
            item["budget"] = dubbing.budget_line(budgets[item["id"]])
    head = "Segmentos (cada um com o tempo que tem para ser falado; não passe dele):" if budgets else "Segmentos:"
    body = (head + "\n" + "\n".join(_segment_line(s) for s in segments)) if segments else (
        "A narração já está traduzida: traduza só os textos de tela.")
    # Pacote travado: o título traduzido é o do candidato escolhido, e o texto da thumb vai junto dos textos de tela.
    chosen = doc.chosen_package() if doc.package.locked else None
    if chosen is not None and chosen.thumb_text and not doc.thumbnail.text.get(target_lang):
        overlays = [*overlays, {"id": PACKAGE_THUMB_TEXT_ID, "text": chosen.thumb_text}]
    title = doc.effective_title()
    prompt = f"""Traduza de {lang_name(source)} para {lang_name(target_lang)}.
Título: {title}
""" + body + (
        "\nTextos de tela (curtos; números com algarismos, adapte a unidade e o separador: 475 MI → 475M, "
        "3,5% → 3.5%):\n" + "\n".join(f"- {o['id']}: {o['text']}" for o in overlays) if overlays else "")
    terms = glossary_prompt(glossary or [], [title] + [t["text"] for t in segments + overlays], target_lang)
    if terms:
        prompt += "\n\n" + terms
    context = {"target_lang": target_lang, "title": title, "segments": segments, "overlays": overlays}
    result, provider_id = _llm("translate", TRANSLATE_SYSTEM, prompt, TRANSLATE_SCHEMA, context, project_id, job_id)
    new_doc = doc.model_copy(deep=True)
    seg_text = {} if screen_only else {item["id"]: item["text"] for item in result.data.get("segments", [])}
    ov_text = {item["id"]: item["text"] for item in result.data.get("overlays", [])}
    thumb_text = (ov_text.pop(PACKAGE_THUMB_TEXT_ID, "") or "").strip()
    if thumb_text:
        new_doc.thumbnail.text[target_lang] = thumb_text[:60]
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
    if style is not None and seg_text and budgets and dubbing.is_multi_track(style, new_doc):
        # Faixas no mesmo vídeo: tradução que passa do tempo vira pausa no vídeo. Pede uma versão mais curta.
        _shorten_over_budget(new_doc, style, target_lang, budgets, project_id, job_id)
    meta = new_doc.metadata.get(target_lang) or Metadata()
    if result.data.get("title") and not meta.title and not screen_only:
        meta.title = result.data["title"]
    new_doc.metadata[target_lang] = meta
    return ProjectDoc.model_validate(new_doc.model_dump()), provider_id


METADATA_SYSTEM = """Você é especialista em SEO de YouTube, TikTok e Reels. Escreva título chamativo e honesto (sem clickbait
enganoso, máx. 90 caracteres), descrição útil (resumo, capítulos se houver, fontes se houver), tags relevantes e hashtags.
O texto da thumbnail deve ter no máximo 4 palavras fortes."""


def metadata(doc: ProjectDoc, style: StyleSpec, lang: str, *, chapters: list[str] | None = None,
             project_id: str | None = None, job_id: str | None = None,
             lessons: list[dict[str, Any]] | None = None) -> tuple[ProjectDoc, str]:
    text = doc.full_text(lang)
    sources = "\n".join(f"- {s.title} {s.url}".strip() for s in doc.sources)
    locked = locked_title_and_thumb(doc, lang)
    prompt = f"""Idioma: {lang_name(lang)}
Plataforma principal: {"YouTube Shorts / TikTok / Reels" if style.format.type == "short" else "YouTube (vídeo longo)"}
Título de trabalho: {doc.effective_title()}
Capítulos (tempo e nome): {", ".join(chapters or []) or "(nenhum)"}
Fontes: {sources or "(nenhuma)"}
Narração completa:
{text[:6000]}"""
    if locked is not None:
        prompt += (f'\n\nO título e o texto da thumbnail já foram escolhidos e não mudam: título "{locked[0]}", '
                   f'thumbnail "{locked[1]}". Escreva só a descrição, as tags e as hashtags (devolva o título e o '
                   "texto da thumbnail iguais a esses).")
    if lessons:
        prompt += "\n\n" + lessons_block(lessons, stage="título, descrição e miniatura")
    context = {"title": doc.effective_title(), "topic": doc.topic, "summary": text[:300], "chapters": chapters or [],
               "lessons_key": lessons_key(lessons or [])}
    if locked is not None:
        context["locked"] = list(locked)
    result, provider_id = _llm("metadata", METADATA_SYSTEM, prompt, METADATA_SCHEMA, context, project_id, job_id)
    data = result.data
    new_doc = doc.model_copy(deep=True)
    description = data.get("description", "")
    if sources and "http" not in description:
        description += "\n\nFontes:\n" + sources
    new_doc.metadata[lang] = Metadata(
        title=(locked[0] if locked is not None else (data.get("title") or doc.title))[:100],
        description=description.strip(),
        tags=[t for t in data.get("tags", []) if t][:30],
        hashtags=[h if h.startswith("#") else f"#{h}" for h in data.get("hashtags", []) if h][:15],
    )
    if locked is not None:
        if locked[1]:
            new_doc.thumbnail.text[lang] = locked[1][:60]
    elif data.get("thumbnail_text"):
        new_doc.thumbnail.text[lang] = data["thumbnail_text"][:60]
    return ProjectDoc.model_validate(new_doc.model_dump()), provider_id


# --- Pacote: título e thumb antes do roteiro (seção A do desenho) ---------------------------------------------------
PACKAGE_THUMB_TEXT_ID = "package#thumb_text"
"""Id do texto da thumb do pacote no pedido de tradução (vai junto dos textos de tela)."""


def locked_title_and_thumb(doc: ProjectDoc, lang: str) -> tuple[str, str] | None:
    """(título, texto da thumb) fixos pelo pacote travado, no idioma `lang`; None sem pacote travado. No idioma
    principal valem os do candidato escolhido; nos outros, a tradução já gravada (ou o original, se não houver)."""
    chosen = doc.chosen_package()
    if not doc.package.locked or chosen is None:
        return None
    if lang == doc.primary_language:
        return chosen.title or doc.title, chosen.thumb_text
    meta = doc.metadata.get(lang)
    title = (meta.title if meta is not None and meta.title else "") or chosen.title or doc.title
    return title, doc.thumbnail.text.get(lang) or chosen.thumb_text


def locked_package_block(doc: ProjectDoc, style: StyleSpec) -> str:
    """O bloco do pedido do roteiro com o pacote travado (título, thumb e promessa); vazio sem pacote travado."""
    chosen = doc.chosen_package()
    if not doc.package.locked or chosen is None:
        return ""
    hook = f"{style.script.hook_max_s:.0f}"
    thumb = f'Thumb: "{chosen.thumb_text}"' + (f", {chosen.concept}" if chosen.concept else "")
    promise = chosen.promise or "(a da thumb e do título)"
    return (f'Título fixo: "{chosen.title or doc.title}"; {thumb}; Promessa: "{promise}". '
            f"Os primeiros {hook}s mostram ou dizem a promessa da thumb; não troque o título.")


# Títulos do pacote (medidos em 17/11 nos longos de 6 concorrentes do nicho, 75 vídeos): os outliers (≥ 5x a mediana do
# canal) têm mediana de 38 caracteres; os vídeos medianos, 54. O título aprovado pelo dono ("GTA 6: sua PRIMEIRA
# SEMANA, dia a dia") tem 37. O manual continua até `PACKAGE_TITLE_MAX` (90).
PACKAGE_TITLE_IDEAL = 45
"""Alvo dos títulos gerados (ideal de 30 a 45 caracteres)."""
PACKAGE_TITLE_TARGET = 50
"""Acima disso a checagem da thumb avisa (título gerado)."""
PACKAGE_TITLE_GEN_MAX = 55
"""Teto rígido dos títulos gerados: acima disso o candidato é descartado (nunca é cortado)."""
PACKAGE_TITLE_CAPS_MAX = 2
"""Palavras em CAIXA ALTA (fora siglas e números) num título gerado."""
THUMB_TOTAL_WORDS_MAX = 3
"""Texto total visível da thumb (palavra grande + pílula + cartões). Nas 16 vencedoras do nicho: metade sem texto e o
resto com 1 ou 2 palavras."""
SEAL_WORDS = {"CONFIRMADO", "CONFIRMADA", "OFICIAL", "IMPRENSA", "BOATO", "RUMOR", "TESTADO", "VERIFICADO", "FATO",
              "SEM SPOILER", "SEM SPOILERS", "SPOILER FREE", "NOVO", "URGENTE", "EXCLUSIVO"}
"""Selos de verificação e rótulos genéricos: não vão na thumb (o selo fica no vídeo)."""

THUMB_RULES = """Regras de thumb (vêm dos vídeos que estouram no nicho: metade das vencedoras não tem texto, o resto tem 1 ou
2 palavras):
- TEXTO TOTAL VISÍVEL de no máximo 3 palavras, somando palavra grande + pílula + cartões. Ideal: 1 ou 2 palavras
  (ou nenhuma). A imagem (rosto, avatar, lugar) carrega o resto.
- "pill" é null e "cards" é [] por padrão. Só use se o conceito depender deles, e eles contam nas 3 palavras.
- Selo de verificação (CONFIRMADO, IMPRENSA, BOATO, TESTADO, OFICIAL) e rótulo genérico (SEM SPOILER, NOVO) não vão na
  thumb.
- numero-lista sem cartões: número gigante + 1 palavra ("7 DIAS", "1ª SEMANA"), com a figura ao lado.
- O texto da thumb complementa o título, não repete: nenhuma palavra em comum de preferência (no máximo 1).
- Protagonista grande: um rosto ocupando 30% ou mais da altura. Cena larga, colagem ou pessoa pequena perdem.
- Um foco só, quadro claro e saturado (pôr do sol, magenta, laranja, verde-água). Nada de tarja preta nem quadro escuro.
- Logo no canto quando houver logo na lista de ativos.
- O avatar do apresentador ajuda quando é grande e expressivo junto de um elemento do tema; pequeno, perde.
- Cada candidato é uma variante real: template, fundo, pose ou palavra diferentes. Nunca dois iguais.
- Fuja das thumbs recentes do canal (a lista vem no pedido): outro fundo, outra figura, outra pose, outro template.
- Vídeo com prova no acervo (captura de matéria, site falso, print): pelo menos 1 candidato usa o template prova com
  o id da captura em background_hint (o print grande com X ou círculo vermelho vende a denúncia melhor que um quadro).
- Personagens oficiais (de jogo, filme ou série) nunca são desenhados por IA: só aparecem em quadros oficiais da lista."""

TITLE_RULES = f"""Regras de título (medidas no nicho: os vídeos que estouram têm títulos de ~38 caracteres; os medianos, ~54):
- Curto: de 30 a {PACKAGE_TITLE_IDEAL} caracteres, nunca mais de {PACKAGE_TITLE_GEN_MAX} (conte; título maior é descartado).
- Uma ideia só, com gancho ou curiosidade. Sem segunda promessa: nada de parênteses com outra promessa, nada de
  "e o que...", nada de listar 3 coisas.
- No máximo {PACKAGE_TITLE_CAPS_MAX} palavras em CAIXA ALTA (siglas e números não contam), na palavra que dá o punch.
- Honesto e cumprível pelo vídeo; nada de clickbait enganoso.
- Ângulos diferentes entre os candidatos, não paráfrases: promessa direta, lacuna de curiosidade, alerta ("NÃO
  faça..."), número/prazo, pergunta, prova pessoal ("testei..."). Diga o ângulo no "concept".
Exemplos de forma (de um canal de GTA; copie a forma, não o assunto):
- Bons: "GTA 6: sua PRIMEIRA SEMANA, dia a dia" (37, o aprovado pelo dono do canal); "GTA 6: NÃO jogue dia 19 sem ver
  o mapa" (38, 57x a mediana); "GTA 6 TROUXE O QUE A GENTE MAIS PEDIA…" (38, 47x); "GTA 6: NÃO jogue igual ao GTA 5"
  (31, 14x); "GTA 6: POR QUE SÓ 30 FPS?" (25, 9x).
- Ruins (longos, duas promessas, sem punch): "GTA 6 já no console: o plano da primeira semana (e o que não comprar
  ainda)"; "GTA 6: o que fazer, o que evitar e o que tem prazo depois de começar"; "GTA 6: itens da Ultimate saem por
  capítulo e o Online fica pra depois"; "GTA 6: upgrade pra Ultimate, 60 FPS e GTA+ antes de abrir a carteira"; "GTA 6:
  testei salvar, o 1º dinheiro e a 1ª garagem (e o golpe do cheat)"."""

PACKAGE_SYSTEM = """Você é o estrategista de embalagem (título + thumbnail) de um canal do YouTube. Antes do roteiro, você
propõe pares candidatos de título e thumb para o tema. O título é curto e tem punch; a thumb tem 1 ou 2 palavras que
são a promessa. A "promise" é uma frase com o que o espectador ganha: ela vira o contrato do gancho do roteiro, então
tem de ser cumprível com o tema.
Templates de thumb: heroi-palavra (quadro com personagem/rosto grande + 1 palavra), avatar-reage (avatar do
apresentador grande reagindo + objeto do tema, 0 a 2 palavras), lugar-mapa (quadro aéreo de um lugar + 1-2 palavras),
numero-lista (número gigante + 1 palavra, sem cartões), versus (dois lados comparados), livre (Short vertical: 2-3
palavras no centro), prova (captura/print do próprio vídeo grande com marcação vermelha + 1 palavra + avatar pequeno;
background_hint = id do acervo), objeto (o item-chave em close + 1 palavra).
Use só os templates e ativos (ids) que o pedido lista; para o que não existir, use null.

""" + TITLE_RULES + "\n\n" + THUMB_RULES

PACKAGE_RESEARCH_MAX = 12
_TEMPLATE_NEEDS_PRESENTER = {"avatar-reage"}
_STOPWORDS = {"de", "da", "do", "das", "dos", "e", "o", "a", "os", "as", "em", "no", "na", "nos", "nas", "um", "uma",
              "para", "pra", "por", "com", "que", "the", "of", "and", "to", "in", "on", "for", "is"}


@dataclass
class PackageAssets:
    """O que a thumb pode usar (o LLM só cita o que está aqui). Itens do manifesto da biblioteca do canal
    (`library/thumbkit/<canal>/thumbkit.json`) e as poses do avatar boas para thumb."""

    frames: list[dict[str, Any]] = field(default_factory=list)
    cutouts: list[dict[str, Any]] = field(default_factory=list)
    logos: list[dict[str, Any]] = field(default_factory=list)
    objects: list[dict[str, Any]] = field(default_factory=list)
    poses: list[dict[str, Any]] = field(default_factory=list)
    """[{id, emotions, aliases, facing}] (vazio: sem avatar nesta thumb)."""
    allow_editorial: bool = False
    presenter_height: float = 1.5
    presenter_crop: str = "busto"
    presenter_outline: int = 10
    proofs: list[dict[str, Any]] = field(default_factory=list)
    """Material do próprio vídeo para a thumb (`project_proofs`): capturas de matéria, prints, imagens do acervo."""
    kit_dir: Any = None
    """Pasta da biblioteca do canal (para medir a cor do fundo atrás do texto)."""
    low_cutouts: list[dict[str, Any]] = field(default_factory=list)
    """Recortes que não servem para thumb (`cutout_defect`: buraco no corpo, corte sem encaixe), com o motivo em
    `defect`. Fora da lista do prompt; o candidato que cita um deles troca pelo do mesmo personagem."""

    def ids(self, kind: str) -> set[str]:
        return {str(item.get("id")) for item in getattr(self, kind) if item.get("id")}

    def prompt_lines(self) -> str:
        def line(items: list[dict[str, Any]], extra: str = "tags") -> str:
            return "; ".join(f"{i['id']}" + (f" ({', '.join(i.get(extra) or [])})" if i.get(extra) else "")
                             for i in items[:30]) or "(nenhum)"

        rows = [f"Fundos (quadros): {line(self.frames)}", f"Recortes de personagem: {line(self.cutouts)}",
                f"Objetos: {line(self.objects)}"]
        rows.append(f"Poses do avatar (com emoções): {line(self.poses, 'emotions')}" if self.poses
                    else "Avatar do apresentador: não usar nesta thumb.")
        if self.proofs:
            rows.append("Material do próprio vídeo (acervo do projeto; para o template prova, o id em background_hint): "
                        + "; ".join(f"{p['id']} ({p['kind']}{' · ' + p['credit'] if p.get('credit') else ''}: "
                                    f"{p['desc']})" for p in self.proofs[:10]))
        if not self.allow_editorial:
            rows.append("Material oficial (quadros de trailer, capa, logos) não está liberado neste canal.")
        return "\n".join(rows)


def thumbkit_manifest(channel_id: str | None) -> list[dict[str, Any]]:
    """Itens do manifesto da biblioteca de thumbs do canal (vazio sem canal, sem manifesto ou com manifesto
    ilegível). Aceita a lista pura ou `{"items": [...]}`."""
    if not channel_id:
        return []
    path = get_settings().library_dir / "thumbkit" / channel_id / "thumbkit.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = raw.get("items") if isinstance(raw, dict) else raw
    return [i for i in items or [] if isinstance(i, dict) and i.get("id")]


def package_assets(doc: ProjectDoc, style: StyleSpec, bible: dict[str, Any] | None) -> PackageAssets:
    """Ativos que a thumb deste projeto pode usar: a biblioteca do canal (sem o material oficial quando o canal não o
    libera) e as poses do avatar, só com o avatar ligado (canal ou projeto) e o estilo pedindo avatar na thumb."""
    allow = effective_allow_editorial(style.thumbnail.allow_editorial, bible)
    items = [i for i in thumbkit_manifest(doc.channel_id) if allow or not i.get("editorial")]
    by_kind: dict[str, list[dict[str, Any]]] = {"frame": [], "cutout": [], "logo": [], "object": []}
    from studio.media.thumb_assets import cutout_defect

    kit_dir = get_settings().library_dir / "thumbkit" / doc.channel_id if doc.channel_id else None
    low: list[dict[str, Any]] = []
    for item in items:
        defect = cutout_defect(item, kit_dir) if item.get("kind") == "cutout" else None
        if defect:
            # recorte que sai torto na thumb (marcado, cortado sem encaixe, buraco no corpo): o LLM nem vê
            low.append({**item, "defect": defect})
            continue
        by_kind.setdefault(str(item.get("kind") or "frame"), []).append(item)
    from studio.core.project import presenter as presenter_core

    presenter = Presenter.from_bible(bible)
    # A mesma regra do resolvedor da thumb (`presenter.effective(purpose="thumb")`): canal/projeto, Short, poses_off.
    active = presenter_core.effective(presenter, doc, style, purpose="thumb") if style.thumbnail.presenter else None
    poses: list[dict[str, Any]] = []
    if active is not None:
        poses = [{"id": p.id, "emotions": p.emotions, "aliases": p.aliases, "facing": p.facing}
                 for p in active.thumb_poses()]
    return PackageAssets(frames=by_kind["frame"], cutouts=by_kind["cutout"], logos=by_kind["logo"],
                         objects=by_kind["object"], poses=poses, allow_editorial=allow,
                         presenter_height=presenter.thumb_height, presenter_crop=presenter.thumb_crop,
                         presenter_outline=presenter.thumb_outline_px, proofs=project_proofs(doc, allow),
                         kit_dir=kit_dir, low_cutouts=low)


PROOF_TIERS = ("own", "capture", "official", "free")
PROOF_MAX = 10


def project_proofs(doc: ProjectDoc, allow_editorial: bool, limit: int = PROOF_MAX) -> list[dict[str, Any]]:
    """O material do PRÓPRIO vídeo que pode virar fundo/prova da thumb: imagens e capturas do acervo do projeto
    (`uploads/<projeto>/pool.json`) que já estão na pasta de dados, sem os quadros da biblioteca do canal (esses já
    são fundos do kit) e sem material de terceiros quando o canal não libera. As capturas (matéria, site falso,
    print) vêm antes; dentro de cada grupo, as que mais falam do tema. Nunca derruba o pacote."""
    if not doc.id:
        return []
    try:
        pool = load_pool(doc.id)
    except Exception:  # noqa: BLE001 - id fora do padrão ou acervo ilegível: sem prova
        return []
    data_dir = get_settings().data_dir
    topic = _topic_words(" ".join([doc.topic or "", doc.title or "", doc.brief or ""]))
    out: list[dict[str, Any]] = []
    for it in pool.items:
        if it.media == "clip" or not it.path or it.path.startswith("library/thumbkit/"):
            continue
        if it.tier not in PROOF_TIERS or (it.editorial and not allow_editorial):
            continue
        if not (data_dir / it.path).is_file():
            continue
        capture = it.media == "capture" or it.tier == "capture"
        desc = " ".join(it.desc.replace("\u2022", " ").split())
        out.append({"id": it.id, "file": it.path, "kind": "captura" if capture else "imagem", "credit": it.credit,
                    "desc": desc[:110], "editorial": it.editorial, "relevance": len(_topic_words(desc) & topic)})
    out.sort(key=lambda p: (p["kind"] != "captura", -p["relevance"]))
    return out[:limit]


def thumb_templates_for(style: StyleSpec, assets: PackageAssets) -> list[str]:
    """Templates que o pacote deste projeto oferece: os do estilo, sem `avatar-reage` quando não há avatar, mais
    `prova` quando o projeto tem material próprio para mostrar e `objeto` quando a biblioteca tem objetos (o objeto
    é o item do kit em close; print de matéria é a prova, não objeto). Short: só `livre`."""
    if style.format.type == "short":
        return ["livre"]
    templates = [t for t in style.thumbnail.templates if t not in _TEMPLATE_NEEDS_PRESENTER or assets.poses]
    templates = [t for t in templates if t != "objeto" or assets.objects]
    if assets.proofs and "prova" not in templates:
        templates.append("prova")
    if assets.objects and "objeto" not in templates:
        templates.append("objeto")
    return templates or ["heroi-palavra"]


def _topic_words(text: str) -> set[str]:
    return {w for w in (normalize_token(t) for t in re.split(r"\W+", text or "")) if len(w) >= 4 and w not in _STOPWORDS}


def package_references(channel_id: str | None, topic: str, limit: int = PACKAGE_RESEARCH_MAX) -> list[dict[str, Any]]:
    """Títulos de outliers para o pedido e para a tela comparar: a última pesquisa do canal (concorrentes e buscas,
    sem Shorts) e o radar (multiplicador contra a mediana do canal no radar; sem mediana, sem `mult`). Só vídeos
    longos; os do radar só quando o título tem alguma palavra do tema. Nunca derruba o pacote."""
    from studio.db import session_scope
    from studio.models import RadarChannel, RadarVideo, ResearchRun

    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(vid: str, title: str, channel: str, views: Any, mult: Any, source: str) -> None:
        if not vid or vid in seen or not title:
            return
        seen.add(vid)
        out.append({"id": vid, "title": title, "channel": channel or "", "views": views,
                    "mult": round(float(mult), 2) if isinstance(mult, (int, float)) else None, "source": source})

    try:
        with session_scope() as s:
            if channel_id:
                run = (s.query(ResearchRun).filter(ResearchRun.channel_id == channel_id,
                                                   ResearchRun.status != "falhou")
                       .order_by(ResearchRun.created_at.desc()).first())
                signals = dict(run.signals or {}) if run is not None else {}
                rows: list[dict[str, Any]] = []
                for key in ("competitors", "keywords"):
                    rows += [r for r in ((signals.get(key) or {}).get("items") or []) if isinstance(r, dict)]
                rows = [r for r in rows if r.get("format") != "short"]
                rows.sort(key=lambda r: -float(r.get("mult") or r.get("ratio") or 0))
                for r in rows:
                    add(str(r.get("id") or r.get("youtube_id") or ""), str(r.get("title") or ""),
                        str(r.get("channel") or ""), r.get("views"), r.get("mult", r.get("ratio")), "pesquisa")
            words = _topic_words(topic)
            if words and len(out) < limit:
                videos = (s.query(RadarVideo).filter(RadarVideo.duration_s > 180)
                          .order_by(RadarVideo.views.desc()).limit(400).all())
                meds: dict[str, float] = {}
                for v in videos:
                    if not (_topic_words(v.title) & words):
                        continue
                    if v.channel_youtube_id not in meds:
                        ch = s.get(RadarChannel, v.channel_youtube_id)
                        recent = dict(ch.recent or {}) if ch is not None else {}
                        med = recent.get("med")
                        meds[v.channel_youtube_id] = float(med) if isinstance(med, (int, float)) else 0.0
                    med = meds[v.channel_youtube_id]
                    add(v.youtube_id, v.title, "", v.views, (v.views / med) if med else None, "radar")
    except Exception as exc:  # noqa: BLE001 - referência é um extra; sem ela o pacote sai igual
        log.warning("não consegui ler as referências do pacote: %s", exc)
    out.sort(key=lambda r: -(r["mult"] or 0))
    return out[:limit]


def _cut_words(text: str, n: int) -> str:
    return " ".join(text.split()[:n])


def _title_overlap(thumb_text: str, title: str) -> tuple[str, list[str]]:
    """Deixa no texto da thumb no máximo 1 palavra que já está no título (as outras saem)."""
    title_words = {normalize_token(w) for w in re.split(r"\W+", title) if w}
    kept: list[str] = []
    repeated = 0
    removed: list[str] = []
    for word in thumb_text.split():
        token = normalize_token(re.sub(r"\W+", "", word))
        if token and token not in _STOPWORDS and token in title_words:
            repeated += 1
            if repeated > 1:
                removed.append(word)
                continue
        kept.append(word)
    return " ".join(kept), removed


def _pick_pose(hint: object, poses: list[dict[str, Any]], used: set[str], recent: Any = None) -> str | None:
    """Pose citada (id, apelido ou emoção) que existe; senão a ainda não usada que o canal menos usou nas thumbs
    recentes (`recent`: `thumb_variety.Usage`); senão None. A citada que o canal já usou nas thumbs recentes (mesmo
    só vista, sem ser escolhida) dá lugar a outra com a mesma emoção e menos uso; sem outra da mesma emoção, à
    menos usada de outra emoção (a mesma cara repetida é o que o público reconhece)."""
    if not poses:
        return None

    def seen(pid: str) -> float:
        return recent.n("pose", pid) if recent is not None else 0.0

    name = normalize_token(str(hint or ""))
    if name:
        for key in ("id", "aliases", "emotions"):
            for pose in poses:
                values = pose.get(key)
                names = [values] if isinstance(values, str) else list(values or [])
                if any(normalize_token(v) == name for v in names) and pose["id"] not in used:
                    if seen(pose["id"]) > 0:
                        emotions = {normalize_token(e) for e in pose.get("emotions") or []}
                        fresher = [p for p in poses if p["id"] not in used and p["id"] != pose["id"]
                                   and seen(p["id"]) < seen(pose["id"])]
                        same = [p for p in fresher
                                if emotions & {normalize_token(e) for e in p.get("emotions") or []}]
                        if same or fresher:
                            return min(same or fresher, key=lambda p: seen(p["id"]))["id"]
                    return pose["id"]
    free = [p for p in poses if p["id"] not in used]
    if not free:
        return poses[0]["id"]
    return min(free, key=lambda p: seen(p["id"]))["id"]


def _frame_layer(frame: dict[str, Any] | None, template: str) -> dict[str, Any]:
    if frame is None:
        return BgLayer(kind="gradient", ref="vice").model_dump()
    ref = str(frame["id"])
    file = str(frame.get("file") or "")
    zoom = 1.4 if "trailer_2" in (ref + file).lower() else 1.0
    focus = frame.get("focus_x")
    if template == "heroi-palavra" and isinstance(focus, (int, float)):
        zoom = max(zoom, 1.5)  # sem recorte: o rosto do quadro vai para o terço direito
    return BgLayer(kind="frame", ref=ref, t=frame.get("t"), zoom=zoom,
                   focus_x=float(focus) if isinstance(focus, (int, float)) else 0.5).model_dump()


_ACRONYM = re.compile(r"^(?:[IVX]+|[A-Z]{1,3}\d*|[A-Z]*\d+[A-Z0-9ªº]*)$")
_SECOND_PROMISE = re.compile(r"\s*\([^()]*\)\s*$")


def _caps_words(title: str) -> list[int]:
    """Índices das palavras em CAIXA ALTA do título (2+ letras), fora siglas curtas (GTA, FPS, PS5, VI) e números."""
    out: list[int] = []
    for i, word in enumerate(title.split()):
        letters = re.sub(r"[\W_]", "", word)
        if len(re.sub(r"\d", "", letters)) < 2 or not letters.isupper():
            continue
        if any(ch.isdigit() for ch in letters) or (len(letters) <= 3 and _ACRONYM.match(letters)):
            continue
        out.append(i)
    return out


def normalize_generated_title(title: str, max_len: int = PACKAGE_TITLE_GEN_MAX) -> tuple[str | None, list[str]]:
    """Título gerado pelo LLM já no formato do pacote, ou None quando ainda passa do teto (`max_len`) e precisa ser
    refeito. Nunca corta no meio: só tira a segunda promessa entre parênteses no fim (quando o resto fica de pé) e
    deixa em caixa alta só as `PACKAGE_TITLE_CAPS_MAX` primeiras palavras gritadas. Devolve também as notas."""
    notes: list[str] = []
    title = " ".join(title.split())
    match = _SECOND_PROMISE.search(title)
    if match:
        rest = title[:match.start()].rstrip(" -–—:,;")
        if len(rest) >= 15:
            notes.append(f"CORRIGIDO: saiu a segunda promessa do título: {match.group().strip()}")
            title = rest
    caps = _caps_words(title)
    if len(caps) > PACKAGE_TITLE_CAPS_MAX:
        words = title.split()
        for i in caps[PACKAGE_TITLE_CAPS_MAX:]:
            words[i] = words[i].lower()
        for i in range(caps[PACKAGE_TITLE_CAPS_MAX], len(words)):  # "antes DE jogar" vira "antes de jogar"
            if words[i].isupper() and re.sub(r"\W", "", words[i]).lower() in _STOPWORDS:
                words[i] = words[i].lower()
        title = " ".join(words)
        title = title[:1].upper() + title[1:]
        notes.append(f"CORRIGIDO: título com {len(caps)} palavras em caixa alta (máximo {PACKAGE_TITLE_CAPS_MAX})")
    if len(title) > max_len:
        return None, notes
    return title, notes


_MONTHS = {"jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6, "jul": 7, "ago": 8, "set": 9, "out": 10,
           "nov": 11, "dez": 12}
_DATE_TAIL = r"(?:\s*/\s*(?P<m>[01]?\d)|\s+de\s+(?P<mn>[a-zç]{3})[a-zç]*)"
_TITLE_DATE = re.compile(
    # prazo: "SOME dia 20", "até o dia 20", "acaba 20/11"; data com mês: "dia 20/11", "dia 3 de dezembro". "dia 1
    # do jogo" e "em 7 dias" não são data
    r"\b(?:at[ée]|antes\s+d[eo]|acaba|termina|expira|vence|some|sai\s+do\s+ar)\s+(?:o\s+)?(?:dia\s+)?"
    r"(?P<d>[0-3]?\d)" + _DATE_TAIL + r"?\b(?!\s*(?:dias?|horas?|semanas?|meses|anos?|fps|%|x)\b)"
    r"|\bdia\s+(?P<d3>[0-3]?\d)(?=" + _DATE_TAIL.replace("?P<m>", "?P<m3>").replace("?P<mn>", "?P<mn3>") + r")"
    r"|\b(?P<d2>[0-3]?\d)\s*/\s*(?P<m2>[01]?\d)\b", re.IGNORECASE)


def title_deadline(title: str, today: date) -> date | None:
    """A data que o título promete ("SOME dia 20", "até 20/11", "dia 20 de novembro"), ou None. Sem mês, vale o mês
    de `today` (ou o seguinte, quando o dia já passou há mais de 15 dias)."""
    m = _TITLE_DATE.search(title)
    if not m:
        return None
    day = int(m.group("d") or m.group("d3") or m.group("d2"))
    month_raw = m.group("m") or m.group("m3") or m.group("m2")
    month_name = m.group("mn") or m.group("mn3") or ""
    month = int(month_raw) if month_raw else _MONTHS.get(month_name[:3].lower(), 0) or today.month
    year = today.year
    try:
        found = date(year, month, day)
    except ValueError:
        return None
    if not month_raw and not month_name and (today - found).days > 15:
        try:
            found = date(year + (month == 12), month % 12 + 1, day)
        except ValueError:
            return None
    elif (today - found).days > 180:  # "20/01" em novembro: é o ano que vem
        found = found.replace(year=year + 1)
    if m.group("d2") and abs((found - today).days) > 60:
        return None  # "24/7" não é data: a forma solta só vale perto de hoje
    return found


def thumb_visible_words(text: str | None, pill: str | None = None, cards: list[Any] | None = None) -> int:
    """Texto total visível da thumb: palavra grande + pílula + rótulos dos cartões."""
    labels = [c.get("label") if isinstance(c, dict) else getattr(c, "label", "") for c in cards or []]
    return sum(len(str(t or "").split()) for t in [text, pill, *labels])


_GENERIC_TAGS = {"claro", "dia", "noite", "keyart", "rosto", "corpo"}
"""Tags que não dizem o assunto do quadro (não contam para achar um fundo "do mesmo tipo")."""


def _subject_tags(item: dict[str, Any] | None) -> set[str]:
    return {str(t) for t in (item or {}).get("tags") or []} - _GENERIC_TAGS


def _is_keyart(item: dict[str, Any] | None) -> bool:
    return "keyart" in set((item or {}).get("tags") or [])


def _uses_keyart(batch: Any, items: dict[str, dict[str, Any]], kind: str) -> bool:
    """O pacote já usa (no `batch`) um item de keyart daquele tipo: a capa oficial e os recortes dela têm a mesma
    arte (o Jason com a arma), então não entram os dois no mesmo pacote."""
    if batch is None:
        return False
    return any(c > 0 and _is_keyart(items.get(v)) for v, c in batch.counts.get(kind, {}).items())


def _pick_frame(hint_frame: dict[str, Any] | None, frames: list[dict[str, Any]], template: str, turn: int,
                recent: Any, batch: Any, need_face: bool, has_figure: bool = False,
                avoid_keyart: bool = False) -> tuple[dict[str, Any] | None, str | None]:
    """O quadro de fundo: o citado pelo LLM quando ele não repete nada das thumbs recentes do canal nem de outro
    candidato (qualquer uso conta: o dono viu todas); senão o menos usado com o mesmo assunto (tags); sem citação,
    o menos usado da biblioteca (o de rosto quando o template precisa de rosto e não tem recorte). Capa (keyart)
    repetida dá lugar a um quadro que não é capa (as capas têm a mesma arte), e com `avoid_keyart` (um recorte da
    keyart já está no pacote) nenhuma capa entra. Devolve (quadro, nota)."""
    if not frames:
        return None, None

    def cost(f: dict[str, Any]) -> float:
        return thumb_variety.penalty("background", str(f["id"]), recent, batch)

    def fits(f: dict[str, Any]) -> bool:
        tags = set(f.get("tags") or [])
        return not (has_figure and {"personagem", "keyart"} & tags) and not (avoid_keyart and "keyart" in tags)

    k = turn % len(frames)
    ordered = frames[k:] + frames[:k]
    if hint_frame is not None:
        clash = avoid_keyart and _is_keyart(hint_frame)
        hint_cost = cost(hint_frame)
        if hint_cost <= 0 and not clash:
            return hint_frame, None
        no_keyart = clash or _is_keyart(hint_frame)
        tags = _subject_tags(hint_frame)
        cands = [f for f in ordered if f is not hint_frame and fits(f) and not (no_keyart and _is_keyart(f))
                 and (clash or cost(f) < hint_cost)]
        light = {"claro", "noite"} & set(hint_frame.get("tags") or [])  # a capa clara dá lugar a um quadro claro
        fallback = ([f for f in cands if light & set(f.get("tags") or [])] or cands) if no_keyart else []
        same = [f for f in cands if tags & _subject_tags(f)] or fallback
        if same:
            pick = min(same, key=cost)
            why = ("tem a mesma arte de um recorte deste pacote" if clash
                   else "repetia as thumbs recentes (ou outro candidato)")
            return pick, f"CORRIGIDO: fundo {hint_frame['id']} {why}; usei {pick['id']}"
        return hint_frame, None
    pool = [f for f in ordered if f.get("face_box") and fits(f)] if need_face else []
    if not need_face:
        pool = [f for f in ordered if fits(f)]
    return min(pool or ordered, key=cost), None


def _framing(item: dict[str, Any] | None) -> str:
    """Enquadramento do recorte: "corpo" (corpo inteiro, rosto pequeno) ou "rosto" (close/busto)."""
    return "corpo" if "corpo" in set((item or {}).get("tags") or []) else "rosto"


def _same_kind(a: dict[str, Any] | None, b: dict[str, Any] | None, template: str) -> bool:
    """`b` substitui `a` na thumb: o mesmo personagem (tag de assunto em comum), a mesma quantidade (solo com solo,
    casal com casal) e o mesmo enquadramento (rosto com rosto); no heroi-palavra, nunca corpo inteiro (o rosto
    some a 168 px)."""
    ta, tb = _subject_tags(a) - {"personagem"}, _subject_tags(b) - {"personagem"}
    if ("casal" in ta) != ("casal" in tb) or not (ta - {"casal"}) & (tb - {"casal"}):
        return False
    if template == "heroi-palavra" and _framing(b) == "corpo":
        return False
    return _framing(a) == _framing(b) or (template == "heroi-palavra" and _framing(a) == "corpo")


def _pick_characters(chars: list[str], cutouts: list[dict[str, Any]], recent: Any, batch: Any,
                     notes: list[str], *, template: str = "heroi-palavra", avoid_keyart: bool = False) -> list[str]:
    """Troca a figura que o canal acabou de usar ou mostrar (qualquer uso conta), que já está em outro candidato,
    que é corpo inteiro no heroi-palavra ou que tem a mesma arte de uma capa do pacote (`avoid_keyart`) por outra
    do mesmo tipo (`_same_kind`: mesmo personagem, solo com solo, rosto com rosto) e menos usada, quando houver."""
    by_id = {str(c["id"]): c for c in cutouts}

    def cost(cid: str) -> float:
        return thumb_variety.penalty("character", cid, recent, batch)

    out: list[str] = []
    for cid in chars:
        item = by_id.get(cid)
        clash = avoid_keyart and _is_keyart(item)
        body = template == "heroi-palavra" and _framing(item) == "corpo"
        if cost(cid) > 0 or clash or body:
            alt = [str(c["id"]) for c in cutouts if str(c["id"]) not in (cid, *out)
                   and _same_kind(item, c, template) and not (avoid_keyart and _is_keyart(c))
                   and (clash or body or cost(str(c["id"])) < cost(cid))]
            if alt:
                pick = min(alt, key=cost)
                why = ("tem a mesma arte da capa usada neste pacote" if clash else
                       "é de corpo inteiro (o rosto some na thumb pequena)" if body and cost(cid) <= 0 else
                       "repetia as thumbs recentes (ou outro candidato)")
                notes.append(f"CORRIGIDO: figura {cid} {why}; usei {pick}")
                cid = pick
        out.append(cid)
    return out


def replace_low_cutout(item: dict[str, Any], cutouts: list[dict[str, Any]], template: str, cost: Any,
                       taken: Any = ()) -> str | None:
    """O melhor recorte bom (`cutouts`, já sem os de qualidade baixa) para ficar no lugar de `item`, um recorte que não
    serve para thumb: o do mesmo tipo (`_same_kind`: mesmo personagem, solo com solo, rosto com rosto; nunca corpo
    inteiro no heroi-palavra) menos usado (`cost`), fora os de `taken`. None quando o personagem não tem outro."""
    alts = [str(c["id"]) for c in cutouts if str(c["id"]) not in set(taken) and _same_kind(item, c, template)]
    return min(alts, key=cost) if alts else None


def _fix_low_cutouts(hints: list[str], low: list[dict[str, Any]], cutouts: list[dict[str, Any]], template: str,
                     recent: Any, batch: Any, notes: list[str]) -> list[str]:
    """Os personagens citados com o recorte ruim (`PackageAssets.low_cutouts`: buraco no corpo, corte sem encaixe)
    trocados pelo melhor recorte bom do mesmo personagem; sem alternativa, o personagem sai (a thumb fica sem a
    figura e o fundo é escolhido como sem personagem). Cada troca ganha um CORRIGIDO."""
    low_by_id = {str(c["id"]): c for c in low}
    out: list[str] = []
    for cid in hints:
        bad = low_by_id.get(cid)
        if bad is None:
            out.append(cid)
            continue
        why = str(bad.get("defect") or "qualidade baixa")
        pick = replace_low_cutout(bad, cutouts, template,
                                  lambda v: thumb_variety.penalty("character", v, recent, batch), (*hints, *out))
        if pick:
            notes.append(f"CORRIGIDO: recorte {cid} não serve para thumb ({why}); usei {pick}")
            out.append(pick)
        else:
            notes.append(f"CORRIGIDO: recorte {cid} não serve para thumb ({why}) e não há outro do personagem; "
                         "a thumb sai sem a figura")
    return out


def _text_side(template: str, recent: Any, batch: Any, turn: int) -> str:
    """Onde vai o texto: o lado que o canal e o pacote menos usaram entre os que o template aceita (o molde inteiro
    espelha: texto à direita, figura e avatar à esquerda). O padrão do template vem primeiro (desempate)."""
    from studio.media import thumb_templates as T

    default = {"livre": "center", "lugar-mapa": "center", "heroi-palavra": "top"}.get(template, "left")
    if template not in T.MIRRORABLE:
        return default
    base = ["top", "left", "right"] if template == "heroi-palavra" else ["left", "right"]
    options = [default] + [o for o in base if o != default]
    return thumb_variety.least_used("side", options, recent, batch, turn) or default


def _fill_for(frame: dict[str, Any] | None, kit_dir: Any, template: str, side: str, recent: Any, batch: Any,
              turn: int, palette: dict[str, str] | None) -> str:
    """Cor do texto pela novidade e pelo contraste com o fundo atrás dele (a região do texto no quadro; na prova, o
    degradê do canal)."""
    from studio.media import thumb_templates as T

    bg_rgb = None
    if template in thumb_variety.CARD_TEMPLATES:
        bg_rgb = (90, 24, 110)  # o topo do degradê vice (atrás da coluna de texto)
    elif frame is not None and kit_dir is not None and frame.get("file"):
        box = (T.spec(template)["text"].get(side) or T.spec(template)["text"]["left"])
        bg_rgb = thumb_variety.region_rgb(kit_dir / str(frame["file"]), _visible_box(frame, template, side, box))
    return thumb_variety.pick_fill(bg_rgb, recent, batch, turn=turn, palette=palette)


def _visible_box(frame: dict[str, Any], template: str, side: str,
                 box: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """A caixa do texto (x, y, w, h no canvas 16:9) em fração do quadro original, com o enquadramento do compositor
    (cover × zoom, o foco do quadro no alvo do template, espelhado com o texto à direita): é ali que a cor do texto
    precisa de contraste."""
    from studio.media import thumb_templates as T

    layer = _frame_layer(frame, template)
    iw, ih = (frame.get("size") or [1920, 1080])[:2]
    W, H = 1280, 720
    geo = T.spec(template)["bg"]
    tx = geo.get("target_x", 0.5)
    tx = 1 - tx if side == "right" else tx
    scale = max(W / iw, H / ih) * max(1.0, float(layer.get("zoom") or 1.0))
    cw, ch = W / scale, H / scale
    left = min(max(0.0, float(layer.get("focus_x", 0.5)) * iw - tx * cw), iw - cw)
    top = min(max(0.0, float(frame.get("focus_y", 0.42)) * ih - geo.get("target_y", 0.42) * ch), ih - ch)
    x, y, w, h = box
    return ((left + x * cw) / iw, (top + y * ch) / ih, (left + (x + w) * cw) / iw, (top + (y + h) * ch) / ih)


def _normalize_candidates(raw: object, *, assets: PackageAssets, templates: list[str], is_long: bool,
                          title_max: int = PACKAGE_TITLE_GEN_MAX,
                          existing_titles: list[str] | None = None,
                          rejected: list[str] | None = None,
                          used_poses: set[str] | None = None,
                          publish_date: date | None = None,
                          history: Any = None, batch: Any = None,
                          palette: dict[str, str] | None = None) -> list[PackageCandidate]:
    """Pós-processamento determinístico dos candidatos do LLM: título curto (`normalize_generated_title`; o que passa
    de `title_max` é descartado e vai para `rejected`, nunca é cortado), texto da thumb até 3 palavras e no máximo 1
    palavra do título, pílula e cartões só quando cabem nas 3 palavras do texto total visível (selo de verificação
    nunca), template permitido, ativos que existem (o que não existe vira o fallback do template: o quadro menos
    usado da biblioteca, ou o degradê), uma pose diferente por candidato e ids `pkc_`. Os avisos ficam em
    `score["notes"]`. Com `publish_date`, título com prazo ("SOME dia 20") anterior à publicação ganha AVISO e vai
    para o fim da lista (o vídeo sairia com o título vencido).

    Variedade (`history`: `thumb_variety.Usage` das thumbs recentes do canal; `batch`: o que este pacote já usou):
    fundo, figura e pose que o canal acabou de usar dão lugar a outros do mesmo assunto; a cor do texto sai da
    paleta de alto contraste (contraste com o fundo + novidade, não só amarelo) e o lado do texto alterna (o molde
    espelha). Captura do próprio vídeo citada em `background_hint` vira o template `prova` (print grande com X ou
    círculo vermelho)."""
    allowed = [t for t in templates if t not in _TEMPLATE_NEEDS_PRESENTER or assets.poses] or ["heroi-palavra"]
    if not is_long:
        allowed = ["livre"]
    frames = assets.frames
    frame_ids = {str(f["id"]): f for f in frames}
    cutouts, objects = assets.ids("cutouts"), assets.ids("objects") | assets.ids("cutouts")
    proofs = {str(p["id"]): p for p in assets.proofs} if is_long else {}
    cutout_items = {str(c["id"]): c for c in assets.cutouts}
    seen_titles = {normalize_token(t) for t in existing_titles or []}
    used_poses = used_poses if used_poses is not None else set()
    recent = history
    batch = batch if batch is not None else thumb_variety.Usage()
    out: list[PackageCandidate] = []
    for item in _list(raw):
        if not isinstance(item, dict):
            continue
        raw_title = " ".join(str(item.get("title") or "").split())
        if not raw_title:
            continue
        title, notes = normalize_generated_title(raw_title, title_max)
        if title is None:
            if rejected is not None:
                rejected.append(raw_title)
            continue
        if normalize_token(title) in seen_titles:
            continue
        seen_titles.add(normalize_token(title))
        deadline = title_deadline(title, publish_date) if publish_date else None
        if deadline and publish_date and deadline < publish_date:
            notes.append(f"AVISO: o título promete um prazo ({deadline:%d/%m}) que vence antes da publicação "
                         f"prevista ({publish_date:%d/%m}); troque o ângulo")
        thumb_text = " ".join(str(item.get("thumb_text") or "").split()).upper()
        if len(thumb_text.split()) > THUMB_WORDS_MAX:
            thumb_text = _cut_words(thumb_text, THUMB_WORDS_MAX)
            notes.append(f"CORRIGIDO: texto da thumb cortado em {THUMB_WORDS_MAX} palavras")
        thumb_text, removed = _title_overlap(thumb_text, title)
        if removed:
            notes.append(f"CORRIGIDO: o texto da thumb repetia o título ({', '.join(removed)} saiu)")
        turn = len(out)
        template = str(item.get("template") or "")
        if template not in allowed:
            if template:
                notes.append(f"CORRIGIDO: template {template} indisponível, usei {allowed[turn % len(allowed)]}")
            template = allowed[turn % len(allowed)]
        hint = str(item.get("background_hint") or "").strip()
        proof = proofs.get(hint)
        # um cartão de captura por pacote: dois prints brancos lado a lado parecem a mesma thumb
        captures_used = any(k.startswith("media:") and c > 0 for k, c in batch.counts.get("background", {}).items())
        fallback = [t for t in allowed if t not in thumb_variety.CARD_TEMPLATES] or ["heroi-palavra"]
        if proof is not None and template != "prova":
            if "prova" in allowed and not captures_used:  # a captura não vira fundo de tela cheia: vira a prova
                notes.append(f"CORRIGIDO: a captura {hint} entrou como prova (template prova)")
                template = "prova"
            else:
                proof = None
        if template == "prova" and proof is None and proofs and not captures_used:
            # sem citação: a captura mais ligada ao tema
            proof = max(proofs.values(), key=lambda p: p.get("relevance", 0))
        if template == "prova" and (proof is None or captures_used):
            why = "já há uma prova neste pacote" if captures_used else "sem material do próprio vídeo"
            notes.append(f"CORRIGIDO: prova {why}; usei {fallback[turn % len(fallback)]}")
            template, proof = fallback[turn % len(fallback)], None
        if template == "objeto" and not assets.objects:
            notes.append(f"CORRIGIDO: objeto sem objeto na biblioteca; usei {fallback[turn % len(fallback)]}")
            template = fallback[turn % len(fallback)]
        card = template in thumb_variety.CARD_TEMPLATES
        hints = [str(c) for c in _list(item.get("characters_hint"))]
        if card:  # o cartão não leva figura: o recorte ruim só sai da lista, sem nota de troca que não acontece
            low_ids = {str(c.get("id")) for c in assets.low_cutouts}
            hints = [c for c in hints if c not in low_ids]
        else:
            hints = _fix_low_cutouts(hints, assets.low_cutouts, assets.cutouts, template, recent, batch, notes)
        chars = list(dict.fromkeys(c for c in hints if c in cutouts))[:2]
        missing = [c for c in hints if c not in cutouts]
        if missing:
            notes.append(f"CORRIGIDO: sem recorte de {', '.join(missing[:3])} (o personagem fica no fundo)")
        if card:
            chars = []
        chars = _pick_characters(chars, assets.cutouts, recent, batch, notes, template=template,
                                 avoid_keyart=_uses_keyart(batch, frame_ids, "background"))
        frame = None
        backdrop = None
        if not card:
            hint_frame = frame_ids.get(hint)
            if hint_frame is None and hint and hint not in proofs:
                notes.append(f"CORRIGIDO: fundo {hint} não existe na biblioteca")
            keyart_cut = (_uses_keyart(batch, cutout_items, "character")
                          or any(_is_keyart(cutout_items.get(c)) for c in chars))
            # o avatar grande também é "a figura": atrás dele, o fundo trocado é um lugar e não outro personagem
            frame, note = _pick_frame(hint_frame, frames, template, turn, recent, batch,
                                      need_face=template == "heroi-palavra" and not chars,
                                      has_figure=bool(chars) or template in ("avatar-reage", "numero-lista"),
                                      avoid_keyart=keyart_cut)
            if note:
                notes.append(note)
        elif template == "prova" and frames:
            # atrás do print, um quadro do jogo (escurecido): a cara de canal de jogo, não um cartão no degradê
            places = [f for f in frames if not {"personagem", "keyart"} & set(f.get("tags") or [])] or frames
            backdrop = str(min(places[turn % len(places):] + places[:turn % len(places)], key=lambda f: (
                thumb_variety.penalty("background", str(f["id"]), recent, batch)))["id"])
        pose_id = None
        wants_pose = template in ("avatar-reage", "numero-lista", "prova") or item.get("presenter_pose")
        if is_long and assets.poses and wants_pose and template != "objeto":
            pose_id = _pick_pose(item.get("presenter_pose"), assets.poses, used_poses, recent)
            if pose_id:
                used_poses.add(pose_id)
        if pose_id and chars and template != "versus":
            # Um foco só: avatar e personagem recortado juntos se atropelam no mesmo lado da thumb.
            if template in ("avatar-reage", "numero-lista"):
                notes.append(f"CORRIGIDO: {', '.join(chars)} saiu (o avatar é o foco desta thumb)")
                chars = []
            else:
                notes.append("CORRIGIDO: o avatar saiu (o personagem é o foco desta thumb)")
                used_poses.discard(pose_id)
                pose_id = None
        # Pílula e cartões: desligados por padrão; ficam só se cabem no texto total visível (3 palavras).
        pill = " ".join(str(item.get("pill") or "").split()).upper()[:24] or None
        if pill and pill in SEAL_WORDS:
            notes.append(f"CORRIGIDO: o selo {pill} não vai na thumb")
            pill = None
        if pill and thumb_visible_words(thumb_text, pill) > THUMB_TOTAL_WORDS_MAX:
            notes.append(f"CORRIGIDO: a pílula {pill} saiu (texto total acima de {THUMB_TOTAL_WORDS_MAX} palavras)")
            pill = None
        cards = [{"asset": c.get("asset") if c.get("asset") in objects else None,
                  "label": " ".join(str(c.get("label") or "").split())[:30]}
                 for c in _list(item.get("cards")) if isinstance(c, dict)][:3] if template == "numero-lista" else []
        if cards and thumb_visible_words(thumb_text, pill, cards) > THUMB_TOTAL_WORDS_MAX:
            notes.append(f"CORRIGIDO: os cartões saíram (texto total acima de {THUMB_TOTAL_WORDS_MAX} palavras)")
            cards = []
        side = _text_side(template, recent, batch, turn) if is_long else "center"
        mirrored = side == "right"
        fill = _fill_for(frame, assets.kit_dir, template, side, recent, batch, turn, palette) if is_long else "yellow"
        if card:
            background = BgLayer(kind="media", ref=f"media:{proof['file']}").model_dump() if proof else \
                BgLayer(kind="gradient", ref="noite").model_dump()
        else:
            background = _frame_layer(frame, template)
        figures = [{"asset": c, "x": 0.72 - 0.18 * i, "y": 1.0, "h": 0.95} for i, c in enumerate(chars)]
        if template == "objeto":
            obj = next((str(c) for c in _list(item.get("characters_hint")) if str(c) in assets.ids("objects")),
                       next(iter(sorted(assets.ids("objects"))), None))
            if obj:
                figures = [{"asset": f"object:{obj}", "x": 0.70, "y": 0.96, "h": 0.86}]
        if mirrored:
            # o molde inteiro espelhado: a figura vai para a esquerda olhando para o mesmo lado de antes (o texto)
            figures = [{**f, "x": round(1 - f["x"], 3), "flip": True} for f in figures]
        layers: dict[str, Any] = {
            "template": template,
            "background": background,
            "big_vi": bool(chars) and template in ("heroi-palavra", "lugar-mapa") and assets.allow_editorial,
            "characters": figures,
            "text": ({"words": thumb_text, "fill": fill, "pos": side,
                      "size": 0.6 if template == "numero-lista" else 0.26 if template == "prova" else 0.3}
                     if thumb_text else None),
            "pill": pill,
            "cards": cards,
            "logo": "corner" if assets.logos and assets.allow_editorial else "none",
            # prova: X no que é falso/perigoso, círculo no resto; objeto em close: círculo (destaca o item)
            "mark": (thumb_variety.mark_for(thumb_text) if template == "prova" else "circle")
            if card and (proof or figures) else None,
            "backdrop": backdrop,
        }
        if pose_id:
            facing = next((str(p.get("facing") or "center") for p in assets.poses if p["id"] == pose_id), "center")
            if template == "prova":
                # avatar grande de corpo embaixo da palavra, do lado de fora do print
                side_p, crop, h = "left", "meio", 0.64
            else:
                # Busto do lado oposto ao texto em todos, menos no heroi-palavra (avatar pequeno de corpo embaixo do
                # texto): do lado do texto, o busto ficaria embaixo da coluna.
                busto = template != "heroi-palavra"
                side_p = ("left" if mirrored else "right") if busto else ("right" if mirrored else "left")
                crop = assets.presenter_crop if busto else "meio"
                h = assets.presenter_height if busto else 0.9
            # a pose que olha/aponta para um lado vira para dentro da thumb (para o texto), nunca para a borda
            layers["presenter"] = {"pose": pose_id, "side": side_p, "crop": crop, "h": h,
                                   "outline_px": assets.presenter_outline, "flip": facing == side_p}
        thumb_layers = ThumbLayers.model_validate(layers)
        repeats = thumb_variety.repeated(thumb_layers, recent)
        if repeats:
            notes.append("AVISO: repete as thumbs recentes do canal: " + ", ".join(repeats))
        batch.add_layers(thumb_layers)
        candidate = PackageCandidate(
            title=title, thumb_text=thumb_text, promise=str(item.get("promise") or ""),
            concept=str(item.get("concept") or ""), layers=thumb_layers, source="auto",
            score={"notes": notes} if notes else {},
        )
        out.append(candidate)
    # título vencido na publicação vai para o fim: os bons ficam na frente da tela
    out.sort(key=lambda c: any(str(n).startswith("AVISO: o título promete um prazo")
                               for n in (c.score.get("notes") or [])))
    return out


def _offline_candidates(doc: ProjectDoc, n: int, templates: list[str],
                        proofs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Candidatos por regra (modo offline/testes, sem LLM): variações curtas do tema (a prova usa a 1ª captura)."""
    base = " ".join((doc.topic or doc.title).split()) or "Vídeo"
    base = base if len(base) <= 30 else (base[:30].rsplit(" ", 1)[0] or base[:30])
    words = ["TUDO", "SEGREDO", "ANTES", "VALE?", "ERRO", "NOVO", "7 DIAS", "FINAL"]
    shapes = ["{b}: o que ninguém conta", "{b} em minutos", "Tudo sobre {b}", "{b}: vale a pena?",
              "O ERRO com {b}", "{b}: antes de começar", "{b}: o guia", "{b} em 7 pontos"]
    return {"candidates": [
        {"title": shapes[i % len(shapes)].format(b=base), "thumb_text": words[i % len(words)],
         "promise": f"Entender {base} sem perder tempo.", "concept": "palavra gigante sobre quadro claro",
         "template": templates[i % len(templates)],
         "background_hint": (proofs[0]["id"] if proofs and templates[i % len(templates)] in ("prova", "objeto")
                             else None), "characters_hint": [],
         "presenter_pose": None, "pill": None, "cards": []}
        for i in range(n)]}


def _retry_block(rejected: list[str], kept: list[str], missing: int) -> str:
    return (f"\n\nREFAÇA {missing} candidato(s). Estes títulos passaram de {PACKAGE_TITLE_GEN_MAX} caracteres e foram "
            f"descartados: {'; '.join(rejected)}. Cada título novo com no máximo {PACKAGE_TITLE_IDEAL} caracteres, "
            f"uma ideia só, ângulo diferente destes que ficaram: {'; '.join(kept) or '(nenhum)'}.")


def package_ideas(doc: ProjectDoc, channel: dict[str, Any], style: StyleSpec, n_titles: int, n_thumbs: int, *,
                  assets: PackageAssets | None = None, references: list[dict[str, Any]] | None = None,
                  lessons: list[dict[str, Any]] | None = None, existing_titles: list[str] | None = None,
                  today: date | None = None, project_id: str | None = None,
                  job_id: str | None = None, publish_date: date | None = None,
                  history: Any = None, kept: list[PackageCandidate] | None = None
                  ) -> tuple[list[PackageCandidate], str]:
    """Candidatos do pacote (título + thumb), já normalizados, e o provedor usado. Pede `max(n_titles, n_thumbs)`
    pares com pelo menos `n_thumbs` conceitos de thumb diferentes. Título gerado acima do teto é descartado e o LLM
    refaz os que faltam uma vez; o que ainda passar fica de fora, com aviso no 1º candidato. No Short: só título e
    texto da capa, template `livre`. `channel` é o de `_channel_info` (nome, nicho, persona, style_bible).
    `publish_date`: publicação prevista (o prompt pede para não prometer prazo que vença antes dela; sem ela, vale
    hoje). Se a 2ª chamada falhar, seguem os candidatos da 1ª (com o aviso dos descartados). `history`
    (`thumb_variety.Usage` das thumbs recentes do canal) entra no pedido e no pós-processamento (fundo, figura, pose,
    cor e lado do texto fogem do que o canal acabou de usar). `kept`: os candidatos que ficam na tela (manuais,
    escolhido, aprovados): os novos também fogem do que eles usam."""
    today = today or date.today()
    publish_date = publish_date or today
    is_long = style.format.type != "short"
    assets = assets or package_assets(doc, style, channel.get("style_bible"))
    templates = thumb_templates_for(style, assets)
    if not is_long:
        templates, n_thumbs = ["livre"], 1
    n = max(1, n_titles, n_thumbs)
    refs = references or []
    ref_lines = "\n".join(
        f"- {r['title']}" + (f" ({r['mult']:.1f}x)" if r.get("mult") else "") + (f" · {r['channel']}" if r.get("channel") else "")
        for r in refs[:PACKAGE_RESEARCH_MAX]) or "(nenhuma)"
    prompt = f"""Data de hoje: {today.isoformat()}
Publicação prevista: {publish_date.isoformat()} (não prometa no título prazo que vença antes disso)
Canal: {channel.get("name") or "(sem nome)"}{f" · nicho: {channel['niche']}" if channel.get("niche") else ""}
Tema: {doc.topic or doc.title}
Título de trabalho: {doc.title}
Briefing: {doc.brief or "(nenhum)"}
Idioma: {lang_name(doc.primary_language)}
Formato: {"Short vertical: só título e texto da capa (2-3 palavras, centro da tela)" if not is_long else "vídeo longo"}
Quantos candidatos: {n} (pelo menos {min(n, n_thumbs)} thumbs de conceito diferente: template, fundo, pose ou palavra)
Títulos: de 30 a {PACKAGE_TITLE_IDEAL} caracteres, nunca mais de {PACKAGE_TITLE_GEN_MAX}; um ângulo diferente por candidato.
Thumb: texto total visível (palavra + pílula + cartões) de no máximo {THUMB_TOTAL_WORDS_MAX} palavras, ideal 1-2; pill null e cards [].
Templates permitidos (ordem de preferência): {", ".join(templates)}
Títulos que já existem (não repita): {"; ".join(existing_titles or []) or "(nenhum)"}
Títulos que estouraram no nicho (referência de forma, não copie):
{ref_lines}
Ativos disponíveis (cite só estes ids):
{assets.prompt_lines()}"""
    recent_line = history.prompt_line() if history is not None and is_long else ""
    if recent_line:
        prompt += ("\n\nThumbs recentes do canal (o que o público já viu; NÃO repita, varie fundo, figura, pose e "
                   f"template): {recent_line}")
    if lessons:
        prompt += "\n\n" + lessons_block(lessons, stage="título, descrição e miniatura")
    context = {"topic": doc.topic or doc.title, "title": doc.title, "n": n, "templates": templates,
               "is_long": is_long, "lessons_key": lessons_key(lessons or [])}
    offline = get_settings().offline
    if offline:
        data, provider_id = _offline_candidates(doc, n, templates, assets.proofs), "offline"
    else:
        result, provider_id = _llm("package", PACKAGE_SYSTEM, prompt, PACKAGE_SCHEMA, context, project_id, job_id)
        data = result.data
    rejected: list[str] = []
    used_poses: set[str] = set()
    batch = thumb_variety.Usage()
    for other in kept or []:
        batch.add_layers(other.layers)
        if other.layers.presenter is not None:
            used_poses.add(other.layers.presenter.pose)
    candidates = _normalize_candidates(data.get("candidates"), assets=assets, templates=templates, is_long=is_long,
                                       existing_titles=existing_titles, rejected=rejected, used_poses=used_poses,
                                       publish_date=publish_date, history=history, batch=batch,
                                       palette=dict(style.thumbnail.palette))
    if rejected and len(candidates) < n and not offline:
        missing = n - len(candidates)
        kept = [c.title for c in candidates]
        try:
            retry, _ = _llm("package", PACKAGE_SYSTEM, prompt + _retry_block(rejected, kept, missing), PACKAGE_SCHEMA,
                            {**context, "retry": rejected}, project_id, job_id)
            still: list[str] = []
            extra = _normalize_candidates(retry.data.get("candidates"), assets=assets, templates=templates,
                                          is_long=is_long, existing_titles=[*(existing_titles or []), *kept],
                                          rejected=still, used_poses=used_poses, publish_date=publish_date,
                                          history=history, batch=batch,
                                          palette=dict(style.thumbnail.palette))[:missing]
        except Exception as exc:  # noqa: BLE001 - a 2ª rodada é bônus: falhou, seguem os bons da 1ª com o aviso
            if not candidates:
                raise
            log.warning("pacote: a chamada que refaz os títulos descartados falhou (%s); seguem %d candidato(s)",
                        exc, len(candidates))
        else:
            candidates += extra
            rejected = still
    if not candidates:
        raise ValueError("o LLM não devolveu nenhum candidato de título e thumb"
                         + (f" (títulos longos demais: {'; '.join(rejected)})" if rejected else ""))
    if rejected:
        log.warning("pacote: %d título(s) gerado(s) acima de %d caracteres descartado(s): %s", len(rejected),
                    PACKAGE_TITLE_GEN_MAX, rejected)
        first = candidates[0]
        notes = [*(first.score.get("notes") or []),
                 f"AVISO: {len(rejected)} título(s) gerado(s) acima de {PACKAGE_TITLE_GEN_MAX} caracteres descartado(s)"]
        first.score = {**first.score, "notes": notes}
    return candidates[:n], provider_id
