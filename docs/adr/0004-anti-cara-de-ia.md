# ADR 0004: tirar a cara de IA dos vídeos

Status: aceito (17/11/2026). Mapeamento em docs/adr/0004/ (relatórios dos leitores).


Nesta fase só li arquivos; nada foi alterado no Vulto nem no canais. Conferi no working tree as linhas-chave abaixo e elas batem com os relatórios: `schema.py:262`, `:335`; `authoring.py:340`, `:402`, `:651`, `:788`; `schemas.py:58`; `document.py:48`, `:315`, `:345`, `:357`; `timeline.py:68`, `:426`, `:431`, `:818`, `:970`, `:1012`; `storyboard_check.py:375-399`; `models.py:567-582`. Duas observações dessa conferência:
- `reference_videos.stats` já é uma coluna JSON. Por isso o perfil pode morar em `stats["profile"]`, sem migração.
- Só existe a migração `0001_base.py`.

## 0. Avaliação dos três desenhos

**GRAMÁTICA (editor primeiro)**
- Pontos fortes:
  - papéis da batida (`hook/reveal/joke/breath`) e o modo de ritmo que sai deles: rajada de planos ou respiro;
  - travas de orçamento: distância mínima entre memes, nada cômico em batida `serious`, no máximo 3 planos seguidos da mesma origem;
  - botão de intensidade;
  - SFX de voz ("ué", "pera") gerados pela própria voz TTS;
  - memória de repetição entre vídeos.
- Falhas e riscos:
  - uma chamada extra de LLM (`edit_marks`) em todo vídeo;
  - `EditState` e o histórico de uso dentro do `ProjectDoc`, o que incha o documento;
  - copia os SFX do canais sem origem rastreável;
  - `build.py` com três donos e `schemas.py` com três donos, o que dá conflito nas worktrees.

**FONTES (real primeiro)**
- Pontos fortes:
  - a melhor hierarquia de fontes (T0 a T7);
  - `SourcePolicy` com teto de IA medido em **tempo de tela**, não em número de cenas;
  - níveis aceitos no gancho;
  - `official_max_share` para limitar risco de Content ID;
  - fundo de cartão vindo de uma sequência de planos reais (reel);
  - colagem híbrida `cutout_on_real`.
- Falhas e riscos:
  - o acervo fica dentro do `ProjectDoc`;
  - o gatilho é `at_word:int`, que quebra quando o texto muda;
  - adiciona Pixabay, que é mais uma chave e tem cache obrigatório de 24 h;
  - propõe pôr SFX do Mixkit no repositório, e o Mixkit proíbe redistribuir o arquivo avulso;
  - a coluna nova exige migração;
  - as transições que dependem do quadro anterior são caras.

**REFERÊNCIA (ciclo medido)** é a escolhida como **base**.
- Pontos fortes:
  - ciclo fechado: referência → perfil → plano → render → impressão digital do vídeo → aderência;
  - confiança e origem guardadas por campo;
  - `pool.json` separado do documento do projeto;
  - gatilho `at` reaproveitando `PRESENTER_AT_RE` (`document.py:315`) e `parse_at`/`trigger_index` (`presenter.py:126,145`);
  - planejador determinístico;
  - "meme sem meme", sem material de terceiros;
  - CLI que mede a linha de base antes de qualquer mudança;
  - botão "novo projeto a partir da referência";
  - teste sintético com vídeo gerado pelo `lavfi`.
- Falhas e riscos:
  - migração `0002` desnecessária;
  - análise pesada (whisper leva de 4 a 7 min);
  - nove itens, com `authoring.py` dividido entre dois deles.

**Enxertos na base**
- Do FONTES:
  - níveis de fonte e `SourcePolicy` por tempo;
  - gancho com níveis aceitos;
  - fundo de cartão com reel real;
  - efeitos por nível (grão só no material de arquivo);
  - `rects` da captura preservados em vez de queimados no PNG.
- Do GRAMÁTICA:
  - `BeatMark` (papel, ênfase, energia, `serious`);
  - modos rajada e respiro;
  - travas de orçamento;
  - intensidade;
  - SFX de voz TTS;
  - memória entre vídeos.
- Decisões de arquitetura:
  - perfil em `stats["profile"]`, sem migração;
  - as marcas de edição entram **no próprio schema do storyboard** como campos achatados, e não numa chamada extra de LLM;
  - "Reeditar" é determinístico e não gasta cota.

---

## 1. Diagnóstico

1. **A IA é a fonte padrão e a única que se preenche sozinha.**
   - Orçamento `max(3, ceil(n × new_image_ratio))` (`authoring.py:340`), com `new_image_ratio=0.5` (`schema.py:335`).
   - O real tem teto `REAL_SHARE = 1/3` (`authoring.py:651`) e é só dica: o prompt diz "IA de reserva" e "o app não baixa nada sozinho" (`authoring.py:269-279`).
   - `STORYBOARD_SOURCES=["new","reuse","none"]` (`schemas.py:58`) não tem fonte real.
   - Nada é anexado sem clique (`api/routes_media.py:129`); nem o piloto automático anexa (`tasks.py:731`).
2. **O gancho e os cartões também são IA.**
   - O gancho é convertido à força em `ai_image` (`storyboard_check.py:380-399`).
   - Todo cartão recebe de fundo a última imagem de IA, desfocada (`authoring.py:859-862`).
   - Resultado: sem anexo manual, 100% da área de imagem é IA.
3. **A IA é proibida de mostrar o assunto real** (`authoring.py:305`, `build.py:163`) e todos os presets usam a mesma estética limpa.
   - Bible "ilustração digital… luz suave" (`schema.py:253`).
   - `World` e `Accent colors` em toda imagem (`build.py:191-200`, `authoring.py:224`).
   - Saem "pinturas digitais" coerentes entre si, com figura de costas. É a assinatura de IA mais reconhecível.
4. **Gramática de slideshow.**
   - Cada frase vira uma cena e um visual (`core/text.py:190`).
   - Ken Burns `inOut(quad)` em tudo (`VisualLayer.tsx:68`, pesos em `schema.py:337`).
   - SFX só na transição (`timeline.py:43-50`, `:431`).
   - Nenhum evento dentro da cena (`SceneView.tsx:183-238`).
   - Efeitos fixos para o vídeo inteiro (`timeline.py:970`): grão e vinheta.
5. **A referência mede só o envelope.**
   - Corte sem áudio (`-an`, `reference_video.py:143-151`).
   - Descarta planos menores que 0,4 s (`:154-163`), que foram 81 na referência do GTA.
   - Folha com 1 quadro de 384 px, ilegível (`:191-214`).
   - O LLM não vê quadro nenhum (`--tools ""`, `providers/llm.py:202-203`).
   - Mesmo que medisse mais, não teria onde gravar: `EditSpec` (`schema.py:332-350`) não tem densidades, e `sources_mix` (`schema.py:262`) é campo morto.
   - Resultado: o estilo gerado saiu com `{"ai_image":1.0}` a partir de um vídeo sem nenhuma IA.

## 2. Princípios

- **O real é o padrão, a IA é apoio declarado** (hipótese, futuro, conceito), em linguagem assumidamente não fotográfica. O teto de IA é medido em tempo de tela.
- **A edição é uma camada própria e determinística** (semente por projeto), separada da cena: vários planos por batida e eventos presos a palavras.
- **A referência é medida e vira alvo numérico**, com confiança por campo. A métrica do vídeo usa o mesmo extrator, então a comparação é de igual para igual.
- **Copiar a fórmula, nunca o material.** Nada de meme ou SFX de terceiros embutido. O que vem de terceiros só entra pela biblioteca do usuário, com licença declarada.
- **Compatibilidade:** todo campo novo é opcional, e a timeline só emite chave nova quando ela está preenchida (padrão de `timeline.py:564-576`). Projeto antigo muda só com storyboard novo ou "Reeditar".
- **Funciona offline** (`VS_OFFLINE=1`, `config.py:41`): SFX procedurais, mocks de LLM e de visão, e acervo-fixture.

## 3. Itens de trabalho

Ordem:
- `card-nome` pode sair já, antes de tudo.
- `fundacao-contratos` vem antes de todos os demais e é o **único dono** de `document.py`, `core/styles/schema.py`, `workers/pipeline/schemas.py`, `renderer/src/types.ts` e `workers/tasks.py`.
- Onda 1, em paralelo depois da fundação: `ref-perfil`, `acervo-real`, `storyboard-real`, `editor-planejador`, `timeline-fx`, `biblioteca-inserts`, `renderer-fx`, `metricas-cara-ia`.
- Onda 2: `perfil-estilo` e `ui-edicao`.
- Ordem de merge: `card-nome` → `fundacao-contratos` → {`timeline-fx`, `renderer-fx`, `biblioteca-inserts`, `editor-planejador`, `metricas-cara-ia`, `ref-perfil`} → `acervo-real` → `storyboard-real` → `perfil-estilo` → `ui-edicao`.

Arquivos compartilhados, sempre em regiões disjuntas:

| Arquivo | Itens e regiões | Merge primeiro |
|---|---|---|
| `from_reference.py` | `card-nome` (:395/:433/:438/:460) e `perfil-estilo` (:223-339) | `card-nome` |
| `workers/visual/references.py` | `card-nome` (:67, :114, `rename`) e `ref-perfil` (função de análise) | `card-nome` |
| `api/routes_visual.py` | `card-nome` (PATCH) e `ref-perfil` (rotas de perfil) | `card-nome` |
| `authoring.py` | `storyboard-real` (:216-1300) e `perfil-estilo` (:130-215) | `storyboard-real` |
| `build.py` | `storyboard-real` (:163-200, :796-831) e `biblioteca-inserts` (:1191-1262) | — |
| `cli.py` | `card-nome` e `metricas-cara-ia`, comandos distintos | — |
| `mock_content.py` | `storyboard-real` (`mock_storyboard`) e `ref-perfil` (`mock_reference_humor`) | — |
| `StyleEditor.tsx`, `References.tsx`, `ReferenceStyle.tsx` | `card-nome` e `ui-edicao` | `card-nome` |

Critério comum a todos os itens:
- `VS_OFFLINE=1 pytest backend/tests` verde e sem rede;
- `npm run typecheck` no renderer e na web quando o item toca TypeScript;
- `test_fase5_renderer.py` passando;
- o projeto fixture antigo gera a timeline com o **mesmo hash de bloco** (`chunk_timeline`, `timeline.py:1012`).

---

### 3.0 `fundacao-contratos`: contratos compartilhados (serial, pequeno, sem lógica)

**Objetivo:** congelar os esquemas e criar pontos de encaixe vazios, para que os outros itens rodem em worktrees sem tocar nos mesmos arquivos.

**Arquivos**

`backend/studio/core/project/document.py`:
```python
Prov = Literal["own","official","capture","free","stock","graphic","meme","ai"]   # nível/origem (tier)
FxType = Literal["punch","shake","zoom_rect","freeze","flash","text_pop","meme","sfx","highlight",
                 "arrow","circle","counter","music_drop","speed","whip","pip"]
class FxEvent(BaseModel):
    type: FxType
    at: str                                   # validado por PRESENTER_AT_RE (:315): "palavra", "palavra#2", "@1200"
    dur_ms: int | None = Field(None, ge=40, le=8000)
    params: dict[str, str | float | bool | list[float]] = {}   # text, scale, color, emoji, rect, lib_id, side, payoff
    origin: Literal["planner","llm","user"] = "planner"
    locked: bool = False
class ShotFraming(BaseModel): scale: float = Field(1.0, ge=1.0, le=1.8); x: float = 0.5; y: float = 0.5
class SubShot(BaseModel):
    at: str | None = None                     # None = início da cena
    source: Literal["self","pool","media","reuse","ai"] = "self"
    pool_id: str | None = None; media: MediaRef | None = None; reuse_of: str | None = None
    framing: ShotFraming | None = None
    motion: Literal["static","push","drift","pan_left","pan_right","ken_burns_in","ken_burns_out","none"] | None = None
    clip_in: float | None = None; speed: float = Field(1.0, ge=0.25, le=2.0); hold: bool = False
    origin: Literal["planner","user"] = "planner"; locked: bool = False
class GagHint(BaseModel): at: str | None = None; payoff: Literal["meme","freeze","zoom","sfx","text"] = "freeze"; tag: str = ""; line: str = ""
class BeatMark(BaseModel):
    role: Literal["hook","reveal","joke","aside","list","number","tension","breath","cta"] = "breath"
    emphasis: list[str] = Field(default_factory=list, max_length=3)
    energy: int = Field(1, ge=0, le=3); serious: bool = False; gag: GagHint | None = None
```
Campos novos nas classes existentes:
- `Scene` (:345): `shots: list[SubShot]=[]`, `fx: list[FxEvent]=[]`, `mark: BeatMark|None=None`.
- `MediaRef` (:48):
  - `kind` passa a aceitar `clip|capture|meme`;
  - campos novos: `video: str|None` (mp4 proxy), `start_s=0.0`, `dur_s: float|None`, `hold=False`, `mute=True`, `rects: list[list[float]]=[]` (x, y, w, h de 0 a 1), `tier: Prov|None`, `pool_id=""`;
  - `asset` continua sendo o JPEG de pôster.
- `MediaSource` passa a aceitar `youtube|library|capture`.

`backend/studio/core/sources.py` (novo, só funções puras):
- `TIERS`, a ordem de reserva `FALLBACK=["own","capture","official","free","stock","graphic","ai"]`;
- `prov_of_scene(scene, doc=None) -> Prov`, a fonte única de verdade usada pela timeline e pelas métricas. Regras (como ficaram na revisão da fundação):
  - cartão de dado (`stat`, `compare`, `chart`, `map`) → `graphic`: o painel cobre o fundo desfocado; os cartões de texto contam pelo fundo;
  - `media.tier` quando existir; senão pelo tipo e pela origem (`prov_of_media`). Link só é `free` com licença livre declarada (CC*, domínio público); sem ela é `official`;
  - com `doc`, a cena que reaproveita outra vale pelo visual da cena dona (raiz `upload` → `own`, `stock` → `stock`); referência solta → `graphic` (fundo animado);
  - `ai_image`/`ai_video` → `ai`;
  - `stock` → `stock`;
  - `upload` → `own`;
  - cartão sem visual → `graphic`.
- `MediaRef` de nível `official` (tipo `official`, clipe de trailer, link sem licença, YouTube) sai sempre com `editorial=True`. A exceção é o arquivo do próprio usuário (`upload`, `library`), em que ele pode declarar que tem os direitos.

`backend/studio/core/project/pool.py` (novo):
- `PoolItem{id:str("p12"), tier:Prov, media:"image|clip|capture|meme", desc, tags, entities, path|None, poster, url, page_url, start_s, dur_s, rects, w, h, license, credit, editorial:bool, dhash, score}`;
- `Pool{items, gathered_at, warnings, searched}` (`searched`: a coleta já rodou o `media_find` pago, e o job não o chama de novo);
- `load_pool(project_id)` e `save_pool(...)` em `<pasta do projeto>/pool.json`. O job grava sempre o resultado da coleta, vazio também, e passa o mesmo `Pool` ao storyboard (`authoring.storyboard(pool=...)`) e ao anexo.

`backend/studio/core/editing/profile.py` (novo):
- `EditProfile` com os blocos `pace`, `camera`, `materials`, `overlays`, `captions`, `audio`, `speech`, `humor`, `recurring` e `meta{campo:{confidence,origin}}`, exatamente como no desenho REFERÊNCIA §1;
- `materials.mix` usa as chaves `gameplay, official_trailer, page_capture, news, meme_reaction, facecam, mascot, photo_real, stock_video, text_card, chart_map, ai_illustration, other`;
- `EditFingerprint` com os mesmos blocos `pace/camera/materials/overlays/audio`;
- `MATERIAL_TO_PROV`: gameplay e official_trailer → `official`; page_capture e news → `capture`; photo_real → `free`; stock_video → `stock`; text_card e chart_map → `graphic`; meme_reaction → `meme`; ai_illustration → `ai`; facecam e mascot → sugerem ligar o apresentador.

`backend/studio/core/styles/schema.py`:
- `ArtSpec.sources: SourcePolicy`, com:
  - `mix={"official":.25,"capture":.15,"free":.15,"stock":.10,"graphic":.15,"meme":.0,"ai":.20}`;
  - `ai_max_share=0.20`, `ai_photo=False`;
  - `hook=["own","official","capture","free"]`;
  - `official_max_clip_s=4.0`, `official_max_share=0.35`, `require_ai_reason=True`.
- Um `model_validator` cobre o legado: estilo sem `sources` e com `sources_mix=={"ai_image":1.0}` recebe a política padrão. Chaves válidas de `sources_mix` são copiadas para `sources.mix`. O campo `sources_mix` fica marcado como obsoleto.
- `sources.mix` sempre soma 1: o que vier é lido como pesos e normalizado na validação (`share(nível)` devolve 0 para nível ausente).
- `EditSpec.grammar: EditGrammar = EditGrammar()`, com:
  - `editor:"off|auto"="auto"`, `intensity:0..3=2`;
  - `shot_median_s=2.6`, `shot_cv=0.6`, `shot_max_s=7.0`, `micro_per_min=1.0`, `burst_prob=0.15`;
  - `punch_per_min=3.0`, `punch_scale=Range(1.12,1.30)`, `shake_per_min=0.3`;
  - `text_pop_per_min=2.0`, `meme_per_min=0` (revisão: padrão conservador; com `humor` ligado e sem valor escrito vale 0,5, a cota das piadas do planejador), `annot_per_min=0.5`;
  - `sfx_per_min=4.0`, `sfx_on_cut_share=0.3`, `sfx_palette:dict[str,float]={}`, `sfx_tonal_ok=True`;
  - `switch_per_min=2.0`, `max_still_s=6.0`, `slow_zoom_share=0.30`, `static_share_min=0.25`;
  - `humor:"none|light|meme"="none"` (revisão: o humor entra por preset ou perfil, nunca por padrão num true crime), `caption_emphasis:"none|color|pop|emoji"="pop"`;
  - `effects_policy:"global|per_tier"="per_tier"`, `music_drop_before_reveal=True`;
  - `hook:HookGrammar|None`, `profile_refs:list[str]=[]`, `tolerance=0.35`.
- Movimento de câmera novo: `push`. A revisão da fundação o tirou de `AUTO_CAMERA_MOVES` (`NOT_AUTO_CAMERA_MOVES`) até o `renderer-fx` desenhar o movimento; com o `renderer-fx` mesclado (`VisualLayer`, aproximação reta no foco), ele voltou ao automático e só o `parallax` fica manual.
- `core/editing/serious.py`: `is_serious(scene, text)` = `mark.serious` OU termo sério do playbook na fala (os termos saíram de `workers/pipeline/presenter.py`). O editor-planejador e o apresentador usam a mesma regra.
- Função pura `grammar_for(style) -> EditGrammar`. No Short, quando o valor está no padrão, ela troca: mediana 1.6, `max_still` 3.0, SFX 6/min, `ai_max_share` 0.30.

`backend/studio/workers/pipeline/schemas.py`, só campos, achatados:
- `STORYBOARD_SOURCES += ["pool"]`;
- `STORYBOARD_EDIT_FIELDS`, por cena: `pool_id: NS`, `ai_reason: NS`, `role: enum`, `emphasis: NSA` (até 3, cortado no `BeatMark`: a saída estrita não aceita `maxItems`), `serious: bool`, `gag_payoff: NS`, `gag_line: NS`, `gag_at: NS`; `STORYBOARD_EDIT_DEFAULTS` dá `role` e `serious` para mocks;
- a revisão da fundação chegou a tirar esses campos do `STORYBOARD_SCHEMA` de produção até o `storyboard-real` explicá-los; com ele mesclado, o pedido do storyboard sempre os explica e eles ficam no esquema de produção;
- os campos `real_kind`, `real_query` e `real_note` (:110) ficam como reserva.

`renderer/src/types.ts`, espelho do contrato **resolvido** (quadros relativos ao início da cena):
```ts
type Prov='own'|'official'|'capture'|'free'|'stock'|'graphic'|'meme'|'ai';
scene.prov?: Prov; scene.clean?: true;
scene.shots?: {from:number;dur:number;prov:Prov;src?:string;video?:string;clipIn?:number;speed?:number;hold?:boolean;
               framing:{scale:number;x:number;y:number};motion:string;easing?:'linear'|'outCubic'|'inOutQuad'|'snap'}[];
scene.fx?: {type:FxType;from:number;dur:number;params:Record<string,unknown>}[];
captions.words[i].emph?: 'color'|'pop'|'emoji'; captions.words[i].emoji?: string;
```
Contrato de áudio na timeline (`timeline.py:426`):
- evento SFX = `{type, start, scene_id, lib_id?, gain_db?}`. São as chaves que `_sfx_event` já emite e que o mixer (`build.py`) e a conferência de áudio leem. `start` está em segundos no vídeo, já descontado o lead. `lib_id` e `gain_db` saem só quando preenchidos. O SFX de evento do `timeline-fx` soma `at` (o ponto forte, na palavra) e `scene`, que a biblioteca de inserts usa para calcular o `start` pelo ataque do som; o SFX de corte antigo sai igual (mesmo hash).
- chave nova `timeline.music_drops=[{at, dur}]`, emitida só quando houver drop (a revisão da fundação propôs `start`; o `timeline-fx`, o editor e a mixagem já usam `at`, e ficou `at`).

`backend/studio/providers/vision.py`:
- interface `classify_frames(sheets:list[Path], schema:dict) -> list[dict]`;
- `MockVision` determinístico, usando saturação e bordas.

Encaixes vazios:
- `workers/pipeline/media_pool.py: gather(doc, style, *, progress=None, cancelled=None) -> Pool` devolve vazio; `attach_chosen(doc, pool) -> list[Issue]` não faz nada;
- `workers/pipeline/edit_plan.py: plan(doc, style, pool) -> list[Issue]` não faz nada;
- `core/editing/metrics.py: fingerprint_from_timeline(timeline, doc) -> EditFingerprint` devolve zeros;
- ligação em `workers/tasks.py`:
  - `gather` antes de `authoring.storyboard` (~`:474`), com progresso ("Juntando material real") e cancelamento do job; o resultado é sempre gravado em `pool.json`;
  - `attach_chosen` e `edit_plan.plan` depois dele (`:484-497`), cada um sobre uma cópia do documento, que só vale se não houver exceção;
  - as `Issue`s deles entram em `storyboard_review.issues`, e as contagens e a mensagem do commit são recalculadas do documento final; falha vira aviso em português, e o detalhe vai para o log;
  - com `pool.searched`, o `media_find` depois do storyboard não roda;
  - o mesmo no piloto (`:731`).

**Contrato com os outros itens:** tudo acima. Ninguém mais edita esses arquivos; se precisar, abre um PR pequeno na fundação.

**Emenda "Ritmo" (revisão do `ref-perfil`, 18/11/2026).** O `PaceBlock` dizia "incluindo os micro-planos", o
`ref-perfil` mediu sem eles e o `metricas-cara-ia` com eles: a timeline e a referência mediam coisas diferentes. Fica:
- **regulares + micro à parte**: mediana, quartis, CV, maior plano e cortes/min são dos planos >= 0,4 s; os micro
  contam em `micro_*` e como evento visual. É a semântica da gramática (`shot_median_s`/`shot_cv` sorteiam os
  regulares, `micro_per_min` põe os micro) e a dos alvos medidos na GTA;
- **pontas sem cortes fora**: janelas de 30 s do início e do fim com taxa < 25% da mediana das janelas saem do ritmo
  (e de `max_still_s`/`events_per_min`); `cuts_per_min_windows` continua do vídeo inteiro, sem a última janela com
  menos de 5 s; o gancho é sempre dos 30 s iniciais;
- **CV robusto**: `sqrt(exp(σ²) - 1)`, σ = desvio do log das durações;
- **um extrator só**: `media/refprofile/cuts.pace(shots, dur)`. A timeline chama
  `cuts.pace(cuts.shots_from_durations([...durações dos planos...]), duração)`; o teste cruzado
  `test_pace_matches_the_timeline_metric` (em `test_refprofile_units.py`) compara os dois;
- `sfx_per_min` sem os da transição (na referência: o whoosh colado no corte) e `kb_share` só sobre o tempo de imagem
  parada, nos dois lados.
Ordem: esta emenda, depois `ref-perfil`, depois `metricas-cara-ia`, que passa a usar `cuts.pace` em `measure_timeline`
(feito na integração das revisões: `measure_timeline` chama `cuts.pace` e o teste cruzado roda).

**Testes:** `backend/tests/test_contracts_compat.py`:
- todos os `presets/styles/*.yaml` e o estilo `refstyle.json` validam;
- o legado `{"ai_image":1}` vira a política padrão;
- `FxEvent.at` inválido é rejeitado;
- o projeto fixture antigo gera timeline idêntica e o mesmo hash de bloco;
- `prov_of_scene` em cada tipo de `Visual`.

**Pronto quando:** os testes passam e os tipos compilam. **Dependências:** nenhuma.

---

### 3.1 `card-nome`: nome do arquivo no card e renomeação

**Objetivo:**
- referência e estilo nascem com nome legível;
- dá para renomear no card e na referência;
- o título não cobre mais o selo nem os botões.

**Arquivos**
- `backend/studio/core/media_title.py` (novo): `clean_media_title`. Usar o código do relatório CARD §4.1; o protótipo está em `C:\Users\lucas\AppData\Local\Temp\claude\367e8e73d2f5afac\d95040d1-a4f5-445b-a986-df4a299967b9\scratchpad\clean_title_test.py`. Opcional: converter para sentence case quando o texto estiver todo em maiúsculas, preservando siglas de até 3 letras e tokens com dígito.
- `workers/visual/references.py`:
  - `:67` e `:114-115` usam `clean_media_title`;
  - `rename_reference(ref_id, title)` novo: vazio dá `ValueError`, id inexistente dá `LookupError`.
- `api/routes_visual.py`: `PATCH /api/references/{ref_id}` com o corpo `{title: 1..300}`. Responde 404 se não achar e 422 se vazio.
- `core/styles/from_reference.py`:
  - `:395` e `:460`: `title = clean_media_title(...) or "Referência"`;
  - `:433`: `[:120]`;
  - `:438`: a descrição usa o título limpo.
- `apps/web/src/apiVisual.ts`: `renameReference(id, title)`.
- `apps/web/src/pages/Styles.tsx`:
  - `:111`: `title={s.name}` com `line-clamp-2 [overflow-wrap:anywhere]`;
  - `:118` e `:178`: as mesmas classes;
  - link "Renomear" abaixo dos chips, apontando para `/estilos/${s.id}#formato`. Não pode haver `<button>` dentro do `<Link>`.
- `apps/web/src/ui.tsx:179` e `:249`: `[overflow-wrap:anywhere]`.
- `StyleEditor.tsx`:
  - `:1390`: `id="estilo-nome"`, `maxLength={120}`;
  - botão "Renomear" no `headerActions` (~`:1128`) quando o estilo não for preset. Ele troca para a aba de formulário se for preciso, vai à seção `formato` e seleciona o input;
  - abrir já na seção quando o hash for `#formato`.
- `ReferenceStyle.tsx:109-135`: `<Field label="Nome do estilo">` com o valor `${reference.title} (referência)` já preenchido; o nome segue em `styleFromReference(..., {use_llm, name})`.
- `References.tsx:142-180`:
  - título com quebra de linha;
  - botão "Renomear" com input inline. Enter salva, Esc cancela, e depois chama `refresh()`.
- `backend/studio/cli.py`: comando `fix-reference-names [--dry-run]`.
  - Aplica `clean_media_title` em `reference_videos.title` quando o valor muda.
  - Em estilos com tag "referencia", `version==1` e `name == f"{old} (referência)"`: troca `name`, `data.name` e `data.description`.
  - **Nunca mexe no id**, porque projetos e canais apontam para ele.

**Contrato:** `clean_media_title(raw:str)->str` é reaproveitado por `ref-perfil` e `acervo-real` para os títulos do yt-dlp.

**Testes:** `backend/tests/test_reference_title.py`:
- os 5 casos da tabela do relatório CARD;
- `save_upload` com o nome do arquivo do YTDown dá "VOCÊ VAI ZERAR GTA 6 PELO YOUTUBE";
- `build_style(use_llm=False)` gera nome sem "YTDown" e com no máximo 120 caracteres;
- PATCH devolve 200, 422 e 404;
- `fix-reference-names --dry-run` não grava nada.

**Pronto quando:**
- o card mostra no máximo 2 linhas e não cobre o selo;
- é possível renomear pelo card, pelo editor e pela referência, e o nome sobrevive ao recarregar;
- o comando corrige os dados atuais do usuário (rodar só com o consentimento dele).

**Dependências:** nenhuma.

---

### 3.2 `ref-perfil`: análise profunda da referência

**Objetivo:** produzir um `EditProfile` medido, com confiança por campo.

**Arquivos**

Pacote novo `backend/studio/media/refprofile/`:

**`cuts.py`**
- `scdet` com dois limiares: 0,30 para corte duro; 0,15 com média móvel de 1 s, para gameplay.
- **Mantém** os planos menores que 0,4 s numa lista `micro`. Muda `reference_video.py:154-163`; `MIN_SHOT_S` continua valendo só para a folha.
- Saídas: mediana, p10, p90, CV, cortes por minuto em janelas de 30 s e `hook_cuts_per_min` (0 a 30 s).

**`motion.py`**
- Quadros em cinza, 160 px, 5 fps por plano.
- Correlação de fase log-polar em numpy para medir escala:
  - salto acima de 8% em até 3 quadros sem corte = **punch**;
  - rampa = zoom lento, em %/s.
- Variância de translação em alta frequência = **shake**.
- Caixa de mudança local estável = **overlay** (meme ou mascote sobre o trailer).
- `freezedetect` para quadro congelado.

**`audio_events.py`**
- Tira o `-an`: extrai mono 22 kHz.
- Fluxo espectral em numpy para achar onsets. Conta como SFX o onset fora das palavras (quando houver transcrição) ou a ±80 ms de um corte.
- Classe grosseira por envelope e centroide: impact, whoosh, pop, riser, scratch.
- `ebur128`/`astats` nos respiros para o nível relativo da trilha.
- Queda acima de 6 dB seguida de pico = drop.

**`speech.py`**
- Usa o `faster_whisper` existente (`providers/align.py:70-121`).
- Sem o pacote, pula o campo e marca `origin:"padrão", confidence:0`.
- Mede palavras por minuto, pausas e perguntas.
- Uma tarefa de LLM nova, `reference_humor`, recebe a transcrição e devolve `structure`, `tone_tags` e `humor.patterns`, com a regra "descreva o padrão, nunca copie frases". O mock vai em `mock_content.mock_reference_humor`.

**`vision.py`**
- Tiras de 3 quadros por plano (início, meio e fim) a 640 px, em lotes de 12 planos.
- Implementação de `providers/vision.py`:
  - `claude -p` com `--tools Read` restrito ao diretório temporário com as folhas, alterando `providers/llm.py:199-203` só para a tarefa `vision_classify`;
  - reserva: `codex exec -i` (`providers/image.py:188-196`).
- Schema por plano: `{material, on_screen_text, text_case, text_pos, font_desc, has_overlay, overlay_kind, is_meme, mood}`.
- Cache pelo sha da tira.

**`recurrence.py`**
- Usa `media/novelty.py` (dHash) sobre as caixas de overlay.
- Agrupamento com 5 aparições ou mais vira `recurring` (mascote, logo ou meme), com miniatura.

**`aggregate.py`**
- Monta o `EditProfile` e o `meta`.
- Várias referências viram um perfil de canal pela mediana campo a campo.

Outros arquivos:
- `workers/visual/tasks.py`: job `reference_profile` depois de `reference_shots` (:22-25), com progresso por etapa.
- Modo `quick=True` (sem visão e sem fala) e modo profundo.
- Grava em `reference_videos.stats["profile"]`; não precisa de migração.
- `api/routes_visual.py`: `POST /api/references/{id}/profile {deep:bool}` e `GET /api/references/{id}/profile`.
- `providers/llm.py`: a tarefa de visão (única alteração).

**Contrato:**
- devolve `EditProfile`, definido na fundação;
- expõe `refprofile.measure_video(path, quick=True) -> EditFingerprint`, usado por `metricas-cara-ia` para auditar o render.

**Testes** (`backend/tests/test_refprofile_*.py`):
- vídeo sintético `lavfi` gerado no tmp: `testsrc2` com cortes em 1,0/2,3/2,6/5,0 s (2,3→2,6 é um micro-plano), `zoompan` 1,0→1,25 em 3 quadros e `sine` de 50 ms em 3,7 s;
- asserções: cortes com ±1 quadro, micro contado, punch detectado, onset com ±60 ms;
- offline: `MockVision` e speech pulado.

**Pronto quando:**
- a referência GTA (`ref_x1l83ap549`) dá, em modo rápido, cerca de 21 cortes/min, mediana ≈1,4 s e cerca de 6 micro/min;
- no modo profundo, a mistura sai com `ai_illustration≈0` e `official_trailer` dominante;
- tudo em cache.

**Validação na GTA (revisão do item, 18/11/2026)** — cópia do vídeo numa pasta de dados do scratchpad:
- O alvo "~21 cortes/min" vinha da análise antiga (`select='gt(scene,0.3)'`, 283 planos), que **perde cortes**.
  Contagem manual de 120 a 180 s (folhas a 6 fps, com 15 fps nos trechos duvidosos): uns 30 cortes duros e 4 fusões
  ou fades. O extrator acha 24 nessa janela (−20%); o que ainda escapa são cortes entre cenas escuras com o mascote
  parado no meio da tela, fusões e fades. Falso positivo achado: um relâmpago no fundo, que virou micro-plano.
  **Alvo corrigido: cortes/min de 26 a 34 no trecho com cortes**; o rápido dá 30,9 (sem a abertura e o
  outro, ver o `PaceBlock`), mediana 1,40 s (alvo mantido) e CV robusto 0,74.
- Micro-planos: 4,4/min. A contagem antiga de 9,25/min transformava toda rajada de 2 candidatos em micro-plano
  (flash de 1 quadro, transição de glitch); agora rajada é transição e só a imagem de 2 quadros ou mais vira micro.
  Na janela manual houve 1 micro-plano real (perdido) e 1 falso (o relâmpago): o alvo "~6/min" não foi confirmado e
  fica como ordem de grandeza.
- Modo profundo (visão com amostra de 48 planos, Claude Code com `VS_VISION_MODEL=sonnet`, 4 lotes, ~50 s; fala com
  faster-whisper small na CPU, ~3 min; humor com 1 chamada): `official_trailer` 0,71, `photo_real` 0,10, `mascot`
  0,06, `meme_reaction` 0,03, `ai_illustration` 0 (o mascote desenhado saiu como `mascot`, sem ajuste no prompt).
  Língua detectada: pt; 195 palavras/min, 2ª pessoa. SFX no profundo: 1,0/min contra 7,8/min no rápido (a
  transcrição só tira falsos positivos: sílaba forte no corte não é SFX).
- Custo do rápido: ~146 s para 13,4 min de 1080p60 (antes 205 s: cortes e movimento agora saem da mesma decodificação).

**Dependências:** fundação; merge depois de `card-nome`.

---

### 3.3 `perfil-estilo`: do perfil ao estilo, ao roteiro e aos presets

**Objetivo:** o perfil vira alvo e vai para o estilo e para o roteiro.

**Arquivos**

`core/editing/grammar.py` (novo), `grammar_from_profile(profile) -> (EditGrammar, SourcePolicy, captions, sfx)`:
- a tolerância cresce quando a confiança é baixa;
- `materials.mix` vai para `sources.mix` pelo `MATERIAL_TO_PROV`;
- `ai_max_share = max(0.05, mix.ai)`;
- se facecam ou mascote for mais de 10%, sugere ligar o apresentador.

`core/styles/from_reference.py:223-339`:
- troca o bloco por `profile_changes`;
- remove o ramo morto `text_ratio` (:284-290);
- o LLM de ajuste (:306-339) passa a receber o perfil, o `script` e o `voice`. Campo medido continua inviolável pelo LLM;
- grava `grammar.profile_refs=[ref_id]`;
- a fonte da legenda sai de `font_match`: catálogo `assets/fonts/fonts.json` com a fonte OFL mais próxima; Pricedown nunca.

`authoring.py:130-215` (`SCRIPT_SYSTEM`, `generate_script`):
- bloco "Voz e estrutura da referência", que só existe com `profile_refs`;
- leva palavras por minuto, pessoa, `tone_tags`, papéis com tempos e 2 a 4 `humor.patterns` abstratos;
- regra: "nenhuma frase da referência".

`presets/styles/*.yaml` (19 arquivos), `sources` e `grammar` por família:
- padrão: `ai_max_share` 0,20;
- `colagem-artesanal`, `whiteboard-explicador` e `explicador-vetorial`: 0,5, porque ali a IA é o estilo assumido;
- `games-cultura-pop-short`: 0,10 e `humor: meme`;
- `dados-e-mapas`: `graphic` alto;
- grão e vinheta só em `per_tier`.

**Contrato:** consome `EditProfile`; escreve só campos que a fundação definiu.

**Testes:** estender `backend/tests/test_style_from_reference.py`:
- um perfil fixture (JSON) gera estilo com `sources.mix.ai≤0.05`, `grammar.shot_median_s≈1.43` e `profile_refs` preenchido;
- sem perfil, cai no comportamento atual;
- todos os presets validam;
- o prompt do roteiro contém o bloco só quando há perfil.

**Pronto quando:** recriar o estilo a partir da referência GTA dá uma política sem IA e a gramática medida.

**Dependências:** fundação e `ref-perfil` (o perfil real; pode desenvolver com fixture); merge depois de `storyboard-real`, por causa de `authoring.py`.

---

### 3.4 `acervo-real`: acervo antes do storyboard e anexo automático

**Objetivo:** existir material real **antes** do LLM do storyboard, e o escolhido ser anexado sozinho.

**Arquivos**

`workers/pipeline/media_pool.py`, `gather(doc, style)`. Coletores independentes; falha vira aviso:
1. Uploads do projeto e biblioteca do canal (`own`).
2. Capturas das `doc.sources`, via `workers/visual/proofs.py` e `media/page_capture.py`. O `page_capture` passa a **devolver os `rects`** medidos em `:159`, sem queimar o destaque no PNG; o PNG limpo vai para o pool.
3. Clipes de links oficiais **dados pelo usuário** no projeto, via `media/clips.py` (novo), que porta e melhora `canais/motor/passos/trailer_shots.py` e `make_clips.py`:
   - yt-dlp como em `reference_video.py:54-67`;
   - cortes com `refprofile/cuts.py`;
   - proxy h264 mudo no fps e na resolução finais, com `-g 15`;
   - modo `hold`;
   - planos de até `official_max_clip_s`;
   - descrição de cada plano por `providers/vision.py`;
   - cache por sha.
4. Fotos do `media_search` (`providers/media_search.py:491`) e **vídeos Pexels** (`/v1/videos/search`, nova `search_videos`; cache pelo `_cache_key` de `:461`; 200 req/h). Mockup de banco com a tela vazia (chroma crua, branca ou desligada) não entra: a descrição/endereço barra na coleta e a imagem baixada (`media/empty_screen.py`: retângulo liso dentro da foto, de cor de chroma ou extrema) manda o anexo para a reserva.
5. `media_find` (`workers/pipeline/media_find.py`) para `official`/`news`: uma chamada por vídeo, com até 15 tópicos extraídos do roteiro (o limite atual é de 8 cenas, `:40`).
6. Anti-repetição: `media/novelty.py` (dHash) penaliza item usado nos últimos 10 vídeos do canal.

`attach_chosen(doc, pool)`:
- cada cena com `pool_id` válido baixa e anexa via `core/project/media_edit.attach_media` (`:44`), preenchendo `MediaRef.tier`, `pool_id`, `video`, `start_s`, `dur_s` e `rects`, mais `editorial` e `credit`;
- quando a cena cai em IA sem `ai_reason`, gera a issue `faltou_real`.

`api/routes_media.py`:
- `GET /projects/{id}/pool`, `POST /projects/{id}/pool/links` (links oficiais) e `POST /projects/{id}/pool/refresh`;
- a troca de candidato continua em `:129`.

**Contrato:**
- produz `pool.json` (`Pool`);
- `media_pool.prompt_block(pool, max_lines=120) -> str` gera as linhas `id · nível · tipo · desc · dur`, agrupadas por tópico, e é consumida por `storyboard-real`.

**Testes:** `backend/tests/test_media_pool.py`, offline, com fixture em `backend/tests/fixtures/acervo/`:
- 2 JPEGs, 1 captura com `rects` e 2 clipes `lavfi`;
- `gather` não toca a rede;
- `attach_chosen` anexa e preenche `tier`;
- `clips.cut` sobre o lavfi com corte conhecido;
- penalidade de novelty;
- `page_capture` devolve `rects`.

**Pronto quando:** num projeto com links e fontes, `pool.json` tem itens dos níveis T0 a T4 e as cenas escolhidas ficam anexadas sem clique.

**Dependências:** fundação; usa `refprofile/cuts.py` de `ref-perfil` (no mesmo nível de onda; até lá, chamar `reference_video.cuts`).

---

### 3.5 `storyboard-real`: storyboard real primeiro e estética da IA que sobra

**Objetivo:** o LLM escolhe do acervo e marca ênfase, papel e gag. A IA só entra com motivo e é estilizada.

**Arquivos**

`workers/pipeline/authoring.py:216-1300`:
- `STORYBOARD_SYSTEM`:
  - reescrever `:248`, com a preferência `pool` específico → `pool` genérico → cartão → `new` (IA), e só com `ai_reason` (hipótese, futuro ou conceito);
  - remover `:269-271` e `:277-279`;
  - `:293`: o gancho é o melhor item `own/official/capture/free` do acervo;
  - `:305`: a proibição vale **só para o prompt de IA**;
  - instruir os campos `role`, `emphasis` (1 a 3 palavras faladas), `serious` e `gag_*`, com a regra "meme é reação à frase (contradição, literalidade, autoironia), nunca decoração; batida séria sem humor".
- `storyboard_prompt` (`:402`):
  - injeta `media_pool.prompt_block`;
  - injeta "Gramática de edição deste estilo", gerada de `grammar_for(style)` em 3 a 5 linhas.
- `_scenes_from` (`:788`):
  - `source:"pool"` válido vira `Visual.media` pendente de anexo;
  - id inválido segue `sources.FALLBACK` no mesmo tópico;
  - `:859-862`: o fundo do cartão vira o item real vizinho (clip ou foto); liso quando o LLM pedir `none`; IA só se a política permitir;
  - preenche `Scene.mark` a partir de `role`, `emphasis` e `gag`.
- `REAL_SHARE`/`_limit_real` (`:651-663`) e `_enforce_budget` (`:1016`):
  - o orçamento passa a ser **tempo** contra `sources.ai_max_share`, estimado pela duração das batidas;
  - o excesso vira `graphic` sobre real, nunca reuse da mesma IA;
  - `image_budget` (`:338-340`) é derivado de `ai_max_share`, sem o mínimo de 3 quando há acervo.
- `authoring.py:224-225`: `World` deixa de entrar em todo pedido.

`workers/pipeline/storyboard_check.py`:
- `:380-399` só converte o gancho em IA se ele não tiver visual **e** o acervo estiver vazio;
- achados novos: `ai_over_budget`, `hook_not_real`, `same_tier_run>20s`, `official_over_share`, `faltou_real` (contagens junto de `:235`).

`workers/pipeline/build.py`:
- `:163-200`: `Accent colors`, `World` e `Treatment` só quando a cena declara entidade ou o estilo pede;
- linguagens novas assumidamente não fotográficas em `core/styles/visual.py`: `collage_cutout`, `ink_sketch`, `halftone_print`, `diagram`, `paper_cut`; variar a linguagem por cena;
- `:796-831` `image()`: `MediaRef.video` (clip) gera pôster e passa o caminho para a timeline.

`workers/pipeline/mock_content.py:90` `mock_storyboard`: com acervo, cita `pool_id`, inclui uma cena `new` com `ai_reason` e preenche ênfase com número ou palavra mais longa.

**Contrato:** consome `pool.json` e `prompt_block`; produz `Scene.mark` e `Visual.media` com `tier`.

**Testes:**
- `backend/tests/test_storyboard_real_first.py`:
  - com acervo-fixture, o gancho não é IA;
  - `ai_time_share ≤ ai_max_share`;
  - nenhum cartão sobre IA quando há real;
  - `pool_id` inválido segue a reserva;
  - sem acervo, a IA entra e `faltou_real` é emitido.
- Atualizar `test_storyboard_v2.py` e `test_real_media_storyboard.py`, que dependem do teto de 1/3 e do gancho em IA.

**Pronto quando:** o storyboard offline com acervo tem IA em até 20% do tempo e o gancho é real.

**Dependências:** fundação; merge depois de `acervo-real`.

---

### 3.6 `editor-planejador`: subplanos e eventos determinísticos

**Objetivo:** transformar as marcas e o acervo em `Scene.shots` e `Scene.fx`, com ritmo humano e orçamento por minuto.

**Arquivos:** `workers/pipeline/edit_plan.py`, implementando `plan(doc, style, pool)` com `seed_from(doc.seed, scene.id, "edit")`.

**1. Ritmo**
- Durações tiradas de uma lognormal com mediana `shot_median_s` e σ=√ln(1+CV²).
- Duração de palavra estimada pelo texto e pela taxa da voz.
- O corte vai para a palavra mais próxima do alvo, nesta preferência: `emphasis` > número > depois de vírgula > substantivo.
- `at` é sempre a string da palavra, com `#n` quando ela se repete.
- Modo pela função da batida:

| Papel | Comportamento |
|---|---|
| `hook`, `joke`, `list` | rajada de 2 a 4 planos de 0,5 a 1,5 s (`burst_prob`) |
| `breath` com clipe | respiro de 5 a 9 s, câmera `none` |
| `reveal` | corte seco na ênfase + `punch` + `sfx impact` + `music_drop` antes, se `music_drop_before_reveal` |

- Plano mínimo de 0,35 s, exceto meme e flash.

**2. Fonte do subplano**, nesta ordem:
- próximo plano do mesmo clipe;
- outro `PoolItem` com a mesma tag;
- o mesmo asset com outro `framing` (jump-cut 1,0→1,25 em outro ponto), no máximo 2 reenquadramentos por asset (imagem de IA: no vídeo todo; material real: por aparição, porque o storyboard real primeiro volta ao mesmo item do acervo de propósito);
- nunca a mesma IA com `zoom_to` mais de 2 vezes.

**3. Câmera**
- Sorteio por plano: static 35%, push 25%, drift 15%, punch seco 15%, pan 10% (só em plano aberto).
- `zoom_strength` com ±40% e easing por semente.
- Clipe e captura com anotação ficam `none`/`static`.
- Respeita `slow_zoom_share` e `static_share_min`.

**4. Cotas por minuto** (com janela própria no gancho) para `punch`, `text_pop`, `sfx`, `meme`, `annot` e `switch`, escaladas por `intensity`: 0→0,5×, 1→0,75×, 2→1×, 3→1,4×.
- Pontuação dos candidatos: ênfase > número > início de capítulo > gag > maior lacuna.
- Guloso, com 1,2 s de espaçamento mínimo.

**5. Gag**
- Se há meme na biblioteca com a tag, entra `meme` de 0,4 a 1,5 s com o SFX dele.
- Senão, entra o "meme sem meme": `freeze` + `punch` 1,3 + SFX `boom` ou `scratch` + `text_pop` com `gag_line`.

**6. `highlight`** em captura com `rects`, na palavra que bate com o trecho (o `zoom_rect` vai junto); **`counter`** nas batidas `list`; **`text_pop`** em número ou palavra-chave.

**7. Travas**
- meme a no mínimo 20 s de distância no longo e 8 s no Short;
- nada cômico em `serious`;
- no máximo 2 planos seguidos com duração a ±15% um do outro;
- no máximo 3 planos seguidos com a mesma `prov`;
- nada nos 0,5 s finais;
- `max_still_s` cumprido com punch, subplano ou `zoom_rect`.

**8. Memória:** penaliza `lib_id` de meme e de SFX usados nos últimos 5 vídeos do canal, lendo o `Scene.fx` dos projetos anteriores.

**9. Preserva** o que é `locked` ou `origin="user"`. Se `grammar.editor=="off"`, não faz nada.

Também: `api/routes_edit.py` com `POST /projects/{id}/reedit {intensity}`, que é determinístico e não gasta cota de LLM nem de imagem.

**Contrato:** escreve apenas `Scene.shots`, `Scene.fx` e `Scene.mark` (só complementa, quando ausente).

**Testes:** `backend/tests/test_edit_plan.py`:
- CV ≥ 0,45;
- todos os `at` batem com `PRESENTER_AT_RE` e com palavras da batida;
- orçamentos por minuto dentro de ±tolerância;
- a mesma semente dá a mesma saída;
- `serious` nunca recebe meme;
- `locked` é preservado;
- `intensity=0` dá menos da metade dos eventos de `intensity=2`;
- `editor=off` não faz nada.

**Pronto quando:** o projeto mock com acervo-fixture sai com subplanos e eventos dentro das cotas.

**Dependências:** fundação.

**Desvios e acertos da revisão** (o código é a referência; a convenção completa está na docstring de `edit_plan.py`):
- `music_drop`: **o gatilho é o FIM da queda**. O evento sai no `at` da palavra da revelação (a do punch), com
  `dur_ms` de 300 a 600, e a trilha some antes dela (contrato do `edit_resolve.resolve_fx`). Só entra junto com o punch
  da revelação e no máximo uma a cada `40 s / escala da intensidade`. A revelação inferida pelo texto exige "!" com
  número ou nome próprio e no máximo uma a cada 20 s; o resto vira `breath`.
- `meme` leva `src` (PNG) ou `video` (WebM) do `MemeItem.file` (relativo à pasta de dados), além do `lib_id`;
  `meme_library.pick` recebe `presenter=` (o avatar efetivo do projeto) e, sem meme, o "meme sem meme" usa
  `template_for` + `template_events`. `shake` grava `strength` e `amount`.
- SFX: o `lib_id` vem de `sfx_library.pick(kind, seed, exclude, tonal_ok)` ou da paleta, sempre conferido na
  biblioteca (licença conhecida, trava tonal, nada cômico em cena séria). Sem a biblioteca, só procedural.
- Cotas: o total de cada tipo é arredondado para o mais perto e só depois dividido entre gancho e corpo (o floor do
  corpo derrubava o Short). A troca de material (`switch`) entra no mesmo orçamento, com a cota do corpo liberada pro
  rata ao longo do vídeo e uma troca reservada para o gancho. O plano de imagem é sorteado até `max_still_s`, para o
  corte cumprir a trava antes do punch.
- Acervo: afinidade exige entidade em comum ou 2 etiquetas; no gancho só os níveis de `sources.hook`; material
  `official` fica em planos de até `official_max_clip_s` (sem respiro longo) e sai dos candidatos ao passar de
  `official_max_share` do tempo de tela. Clipe sem `dur_s` não ganha "próximo trecho".
- Contrato da fundação ampliado neste item (PR pequeno, campos opcionais): `SubShot.easing`/`SubShot.strength` e
  `BeatMark.origin` (`planner` na marca inferida, refeita a cada "Reeditar"). `core/editing/stale.drop_stale_edit`
  tira os subplanos e eventos do planejador (não travados) e a marca inferida da cena cuja fala ou mídia mudou; é
  chamado por `ops.apply_ops`, pelas edições de `media_edit` e pelo `PUT /projects/{id}/doc`.
- **Pendência de integração com `timeline-fx`/`renderer-fx`:** `SubShot.easing` e `SubShot.strength` só chegam ao
  vídeo quando `edit_resolve.resolve_shots` usar o `easing` do plano (e só sortear quando vier None) e multiplicar a
  força de zoom do estilo pelo `strength`, emitindo `strength?` no plano resolvido (`types.ts`) para o renderer. Até lá,
  a variação de força fica sem efeito. O punch seco não depende disso: é o corte para um enquadramento fechado e parado.
- Créditos dos subplanos do acervo: feitos em `timeline-fx` (`edit_resolve.shown_materials` em
  `credits.shown_media`), que entra antes deste item na ordem de merge.

---

### 3.7 `timeline-fx`: resolver planos e eventos em quadros

**Objetivo:** a timeline converte `shots` e `fx` para o contrato resolvido e aplica a câmera e os efeitos novos.

**Arquivos:** `core/timeline.py`, mais `core/edit_resolve.py` (novo).

`edit_resolve.resolve(scene, words, fps) -> {shots, fx, sfx, music_drops}`:
- usa `presenter.parse_at` e `trigger_index` (`presenter.py:126,145`) como em `timeline.py:775-785`;
- gatilho que não bate cai no início da cena e gera um aviso;
- `prov` vem de `sources.prov_of_scene` ou do item.

`resolve_scene` (:818):
- emite `shots`, `fx`, `prov` e `clean` **só quando preenchidos**;
- `clean=True` quando `effects_policy=per_tier` e a `prov` for `capture`, `graphic` ou `own`.

Áudio:
- `_scene_sfx` (:431) junta os SFX de evento no formato que já existe, `{type, start, scene_id, lib_id?, gain_db?}`, mais `at` e `scene` (ver §3.0);
- o SFX de transição continua igual quando não há `grammar` ativa.

Câmera:
- `REAL_MOTIONS` (:68) ganha `static` e `push`;
- `scene_motion` (:701) respeita `slow_zoom_share` quando a cena não tem `shots`.

Outros:
- `render_effects` (:970) continua global, e o renderer lê `clean` por cena;
- legendas: `words[i].emph` para as palavras em `mark.emphasis`, conforme `grammar.caption_emphasis`;
- `timeline.music_drops`.

**Contrato:** o formato de `types.ts` da fundação.

**Testes:** `backend/tests/test_timeline_edit.py`:
- sem campos novos, o hash não muda;
- `at` "palavra#2" resolve o quadro certo;
- `@1200` resolve em ms;
- evento fx gera SFX;
- `clean` aparece por nível;
- `emph` cai nas palavras certas.

**Pronto quando:** a cena com `shots` e `fx` gera o JSON resolvido válido para o renderer.

**Dependências:** fundação.

---

### 3.8 `biblioteca-inserts`: SFX e memes com licença registrada

**Objetivo:** vocabulário sonoro e de reação sem risco de direitos, funcionando offline.

**Arquivos**

`media/audio.py`:
- `SFX_KINDS` (:344) ganha procedurais `pop`, `tick`, `click`, `thump`, `boom` (varredura senoidal grave com cauda), `scratch` (ruído filtrado em varredura), `shutter`, `swish`, `bass_drop` e `riser_short`;
- `SFX_VERSION` sobe;
- `generate_sfx` (:448) resolve primeiro a biblioteca, depois o procedural, e só então whoosh (:445).

`media/sfx_library.py`:
- manifesto `{id, file, kind, tags, license, source_url, author, hit_s, dur, lufs, tonal}`;
- fontes: `assets/sfx/vulto-basico/manifest.json` (só itens procedurais ou CC0) e `library/sfx/` do usuário;
- **trava tonal** na importação: pico espectral e planura em numpy; som tonal só entra no automático com `sfx_tonal_ok`;
- licença "desconhecida" fica fora do automático e gera aviso.

`build.py:1191-1262`:
- mixagem por `lib_id` e `gain_db`;
- `music_drops` viram duck de −∞ por 0,3 a 0,6 s, somado ao sidechain já existente (`audio.py:490`).

`workers/canais_import/sfx.py`:
- importa os .wav de `C:/Users/lucas/canais/plantao-vice/video/public/sfx` para `VS_DATA_DIR/library/sfx`. Só leitura no canais; nada vai para o repositório;
- licença `mixkit-free` para os IDs rastreados (`motor/passos/get_audio.py:48-52`) e "desconhecida" para o resto.

SFX de voz:
- "ué", "pera", "oxe" e "hã?" gerados pela voz TTS do canal (Kokoro local), em cache;
- licença `own`.

`media/meme_library.py`:
- `library/memes/` com manifesto `{id, file(png|webm alfa), tags, license: own|cc0|cc_by|fair_comment, risk, max_ms, sfx_id}`;
- upload com licença obrigatória;
- templates "meme sem meme" (freeze, zoom, vinheta, legenda) definidos como parâmetros, sem arquivo;
- as poses do apresentador viram reações.

Rotas: `api/routes_library.py` com GET, POST e DELETE para SFX e memes, mais `POST /library/sfx/fetch-freesound` (só CC0, com a chave do usuário).

`scripts/fetch_sfx.py`: roda manualmente pelo usuário para Freesound CC0.

**Contrato:** `sfx_library.resolve(lib_id|kind) -> Path`; `meme_library.pick(tag, seed, exclude) -> MemeItem|None`, usado por `editor-planejador`.

**Testes:**
- `backend/tests/test_sfx_library.py`:
  - todos os procedurais são gerados offline e com duração correta;
  - a trava tonal rejeita um seno puro e aceita ruído;
  - licença desconhecida fica fora do automático;
  - `resolve` cai no procedural.
- `test_meme_library.py`: upload sem licença dá 422.
- O `test_audio_mix.py` continua verde.

**Pronto quando:** o vídeo offline tem SFX de evento audíveis e nenhum arquivo de terceiros entra no git.

**Dependências:** fundação.

---

### 3.9 `renderer-fx`: novas primitivas do Remotion

**Objetivo:** desenhar os planos, a câmera e os eventos.

**Arquivos em `renderer/src`**

`components/CameraRig.tsx`:
- envolve o **fundo** em `SceneView.tsx:185-212`;
- soma keyframes de `punch` (sobe em 2 a 3 quadros, segura e solta no próximo corte), `shake` com decaimento e `zoom_rect` (câmera até o retângulo);
- não mexe no interior das camadas.

`components/ShotStack.tsx`:
- uma `Sequence` por `scene.shots`;
- cada plano usa `VisualLayer` ou `ClipLayer` com `framing`, `motion` e `easing`;
- corte seco, ou `whip` (blur direcional) quando houver fx `whip`;
- sem `shots`, o caminho é o atual.

`components/fx/FxLayer.tsx`, entre `EffectsLayer` e o cartão (`SceneView.tsx:213`), abaixo de `Captions`. Despacha:
- `TextPop`: 1 a 3 palavras gigantes, bounce, rotação de ±4°, cor de destaque, sem escurecer o fundo;
- `MemeInsert`: png ou webm com alfa, mola, `stickerFilter` extraído de `PresenterLayer.tsx:29` para `lib.ts`, variantes `full`, `corner` e `freeze_zoom`;
- `Highlight`: marca-texto varrendo o `rect` na palavra; é o porte do `WebShot`, `canais/plantao-vice/video/src/web.tsx:29-87`;
- `Annot`: seta e círculo em SVG com `stroke-dashoffset`;
- `Counter`: "3/10" no canto;
- `Flash`, `Freeze` e `Pip`.

Camadas alteradas:
- `ClipLayer.tsx`: toca `MediaRef.video` em qualquer `kind` (hoje só o vídeo de IA, `:32`), com `OffthreadVideo`, `startFrom`, `playbackRate`, `hold` (congela no último quadro) e mudo.
- `VisualLayer.tsx`: `push`/`static` e easing variável em `:68`; o `punch_zoom` do quadro 0 continua por compatibilidade. Com o `push` desenhado, ele saiu de `NOT_AUTO_CAMERA_MOVES` (`core/styles/schema.py`).
- `EffectsLayer.tsx`: pula grão e vinheta quando `scene.clean`.
- `Captions.tsx`: `emph` com cor de destaque, escala 1,3 e emoji (Noto Emoji). Substitui o realce de +6% de `:228`.
- `sample.ts`: cena de exemplo com todos os eventos, todos os `shots` e um clipe.

**Contrato:** só lê `types.ts`.

**Testes:** `npm run typecheck`; `test_fase5_renderer.py` com o `sample.ts` novo; captura de quadro em pontos fixos (punch no quadro N mostra escala maior que 1,1).

**Pronto quando:** o sample renderiza sem erro e o hash dos blocos de cena sem campos novos não muda.

**Dependências:** fundação.

---

### 3.10 `metricas-cara-ia`: medição, conferência e linha de base

**Objetivo:** dar um número à "cara de IA", antes de gastar cota e depois do render.

**Arquivos**
- `core/editing/metrics.py`:
  - `fingerprint_from_timeline(timeline, doc)`, exato e anterior ao render;
  - `ai_look_index(fp, style)`;
  - `adherence(fp, profile)`.
- `workers/finalcheck/profile_check.py`:
  - depois do render, roda `refprofile.measure_video(mp4, quick=True)`;
  - grava um `QualityReport` com `kind="edit"` (string livre, sem migração, `models.py:585+`);
  - itens `perfil.ia`, `perfil.ritmo`, `perfil.material`, `perfil.sfx`, `perfil.eventos`, `perfil.parado`, `perfil.repeticao` e `perfil.licenca`, com `at_s` e `scene_id`;
  - registro em `finalcheck/report.py`.
- `workers/finalcheck/quick.py`: os mesmos itens a partir da timeline.
- `api/routes_checks.py`: `GET /projects/{id}/edit-metrics`.
- `cli.py`: `studio edit-metrics <projeto|--all>`, só leitura. **Rodar antes de tudo** nos 4 projetos GTA de hoje para registrar a linha de base.
- `apps/web/src/pages/project/EditScore.tsx`: componente; a montagem na tela fica com `ui-edicao`.

**Testes:** `backend/tests/test_edit_metrics.py`:
- timeline sintética 100% IA com Ken Burns dá índice acima de 70;
- timeline real-first com CV 0,7 dá índice abaixo de 30;
- aderência 100 para o próprio perfil;
- o CLI não grava nada.

**Pronto quando:** o CLI mostra a linha de base dos 4 projetos e o finalcheck emite os itens.

**Dependências:** fundação; usa `refprofile` para auditar o render (stub até o merge de `ref-perfil`).

**Como ficou (desvios registrados na revisão):**
- A timeline do vídeo final só vale no `auto` quando bate com as cenas do documento e é da versão atual; senão, a prévia nas mesmas condições ou a estimada. A resposta diz `outdated`, `version` e `current_version`. A timeline de outra versão é medida com o documento daquela versão.
- `kb_share` mede todo movimento lento sobre foto parada (Ken Burns, pan, `zoom_to`, `push`, `drift`, parallax). O teto não é o `slow_zoom_share` puro: é o que o sorteio de câmera do planejador permite (`metrics.kb_cap`: zoom lento mais a fração do resto que também é movimento lento). Com a gramática padrão e a câmera do estilo padrão, isso dá 0,75. Se `timeline-fx` passar a limitar pan e deriva junto (`edit_resolve.SLOW_ZOOMS` = `metrics.SLOW_STILL_MOTIONS`), o teto cai sozinho para o `slow_zoom_share`.
- `events_per_min` usa a faixa alvo ÷/× (1 + tolerância) nos dois sentidos, no índice e no item.
- `repeat_hits`: a mesma imagem em mais de 3 cenas (o reaproveitamento planejado do storyboard é livre até aí), o mesmo meme de novo no vídeo ou nos últimos vídeos do canal (janela do `novelty`), e o mesmo SFX da biblioteca acima de max(2, 1 por minuto). O dHash entre vídeos continua no aviso `novelty` da conferência final, e não no índice.
- SFX no formato antigo (sem `at`): o som no início da própria cena conta como de corte; o carimbo e o reveal, dentro da cena, contam como evento.
- Auditoria do MP4: só ritmo e câmera vêm do vídeo. O áudio fica o da timeline, e o item `perfil.render` avisa quando o MP4 diverge dela. Com o vídeo final em dia e o relatório auditado fresco, a rota devolve os números do relatório (`measured_from: "render"`).

---

### 3.11 `ui-edicao`: telas e integração final

**Objetivo:** o usuário vê e corrige o que o editor fez e o que a referência mediu.

**Arquivos (`apps/web/src`)**
- `pages/references/ReferenceProfile.tsx` (novo):
  - perfil com barras de mistura de material, ritmo, densidades, recorrentes e confiança por campo;
  - botões "Analisar a fundo", "Criar estilo" e **"Novo projeto a partir desta referência"** (cria o estilo e o projeto com nome limpo e editável).
- `pages/StyleEditor.tsx`:
  - seção "Gramática de edição" com intensidade, cotas e humor;
  - seção "Fontes" com a mistura em `sources` e o teto de IA;
  - esconder `sources_mix` e `new_image_ratio` (`StyleEditor.tsx:134`);
  - a fundação já pôs `art.sources.mix` e `edit.grammar.sfx_palette` em `OPEN_MAPS` (apagar um nível não volta mais em silêncio).
- Cena: `push` em `MOTIONS` (`ScenesTab.tsx:59`), com o rótulo "Aproximação reta" em `CAMERA_LABELS`. Os rótulos de `clip`/`capture`/`meme` e das origens `youtube`/`library`/`capture` já estão em `labels.ts`, e o select "Tipo" da mídia fica só leitura nesses tipos.
- Projeto:
  - painel "Acervo": lista do `pool.json`, colar links oficiais, trocar candidato;
  - `EditScore` montado na Prévia e na revisão do storyboard (o "Trocar IA por real" usa `ai_scene_ids`, e com `outdated` a tela diz que o projeto mudou depois da medida);
  - Conferência final: mostrar `preflight.edit.items` (os `perfil.*` do `QualityReport kind="edit"` em dia com o MP4, no máximo aviso, com `at_s` clicável), à parte e sem entrar no `status`;
  - `EditTrack.tsx`: por cena, os subplanos e eventos, com travar, apagar e mover gatilho por `PATCH /projects/{id}/scenes/{sid}/fx` e `/shots` (rotas em `api/routes_edit.py`);
  - botão "Reeditar" com intensidade.
- `apiVisual.ts` e `api.ts`: as chamadas.

**Teste de ponta a ponta:** `backend/tests/test_anti_ia_e2e.py`, offline: roteiro mock → gather (fixture) → storyboard → planejador → timeline → fingerprint. Asserções:
- `ai_screen_share ≤ 0.25`;
- `shot_cv ≥ 0.45`;
- SFX/min entre 2 e 10;
- gancho com `prov != "ai"`;
- `max_still ≤ 6 s`.

**Pronto quando:**
- o usuário cria um projeto a partir da referência GTA;
- vê o índice cair;
- consegue reeditar e travar eventos.

**Dependências:** todos os anteriores; merge por último.

---

## 4. Assets no repositório

**Pode entrar no repositório**
- **SFX procedurais** (`media/audio.py`): código próprio. Opcionalmente, os WAVs pré-renderizados em `assets/sfx/vulto-basico/` com `license: own`.
- **Fontes:** Google Fonts em `assets/fonts` (já há Anton, Bebas, Montserrat e Oswald), sempre com o arquivo de licença ao lado:
  - Bangers, Archivo Black (OFL);
  - Luckiest Guy, Permanent Marker (Apache 2.0);
  - Noto Emoji (OFL).
- **SFX do Freesound marcados CC0:** podem entrar, um por um, com `source_url` e autor no manifesto. A curadoria é manual. O download do original exige OAuth (30/min, 500/dia); a prévia não exige.

**Só sob demanda, nunca no git**
- **Mixkit:** a licença permite uso comercial e YouTube, mas não a redistribuição do arquivo avulso. Entra via `fetch_sfx.py` ou importação do canais, na pasta de dados do usuário, item a item, conferindo que não é "Restricted".
- **SFX do canais** sem origem (click, pop, stamp, type, whoosh, static, bleep): importados como "desconhecida", fora do automático.
- **Memes:** nenhum embutido.
  - Biblioteca do usuário com licença declarada.
  - Reações do próprio avatar.
  - Templates "meme sem meme" (parâmetros, sem arquivo).
  - **Não integrar** Tenor (API encerrada em 30/06/2026) nem GIPHY (o GIF não licencia a obra de origem).
  - **Nunca** extrair meme da referência para reutilizar.
- **Pricedown (fonte do GTA):** licença não verificada. Só se o usuário instalar e declarar a licença; o padrão é a OFL mais próxima.
- **Trailer e gameplay oficial:** só por link do usuário.
  - `editorial=True`, mudo, planos de até 4 s, crédito automático, `official_max_share` 0,35;
  - aviso de Content ID (a política da Rockstar de 01/2025 é "não comercial");
  - o bloqueio do Short acima de 60 s já existe (`finalcheck/measure.py:33`).

## 5. Fica para depois ou depende do usuário

**Depende do usuário**
- Instalar `faster-whisper`: `uv sync --extra asr`; o modelo small já está no cache do HF. Sem ele, `speech` fica com confiança 0.
- Chaves de API:
  - Pexels, se ainda não estiver em `providers/credentials.py`; necessária para vídeo livre;
  - Freesound (CC0), opcional.
- Links de trailer e gameplay oficiais por projeto, e a decisão de risco de Content ID.
- Biblioteca de memes própria.
- Rodar `fix-reference-names` sobre os dados reais, com o consentimento dele.
- Confirmar a licença da Pricedown.

**Fase 2**
- Pixabay (cache de 24 h, veta download em massa).
- Colagem híbrida `cutout_on_real` (rembg já instalado).
- Transições que dependem do quadro anterior (slide e push reais).
- Split screen JOGO x VIDA REAL.
- TransNetV2, PaddleOCR, Demucs e audfprint, para mais precisão na referência.
- Cartelas e CTA (porte de `chapters.tsx` e `cta.tsx` do canais).
- IA fotorrealista com rótulo do YouTube.
- Medir o custo de render: estimativa de +30% a 60% com vídeo e várias `Sequence`.

## 6. Métricas de "cara de IA" e onde aparecem

Todas saem de `fingerprint_from_timeline`. As do render saem do mesmo extrator da referência.

| Métrica | Definição | Alvo longo / Short |
|---|---|---|
| `ai_screen_share` | tempo de tela com `prov=="ai"` ÷ duração | ≤ `ai_max_share` (0,20 / 0,30) |
| `ai_window_share` | a maior fração de IA num trecho de `sources.AI_WINDOW_S` (45 s) | ≤ `sources.ai_window_cap` (1,5 × `ai_max_share`: 0,30 / 0,45) |
| `hook_real` | os 3 s iniciais não são IA, havendo acervo | verdadeiro |
| `real_share` | tempo `own+official+capture+free+stock` | ≥ 0,5 |
| `shot_median`, `p10`, `p90`, `shot_cv` | pelos subplanos | perfil, ou 3–5 s / 1,2–2,5 s; CV ≥ 0,45 |
| `kb_share` | tempo de imagem com ken_burns ou pan | ≤ `slow_zoom_share` (0,30) |
| `events_per_min` | cortes + punch + fx + inserts | perfil ±tolerância |
| `sfx_per_min` | sem contar transição | 2–5 / 4–10, ou o perfil |
| `switch_per_min`, `max_same_prov_run` | troca de **tipo** de mídia | ≥ 2 / ≥ 4; sequência ≤ 3 planos ou 20 s |
| `max_still_s` | maior trecho sem evento visual | ≤ 6 s / ≤ 3 s |
| `repeat_hits` | dHash, meme ou SFX repetidos no vídeo e nos últimos N do canal | 0 |
| `official_share` | tempo T2 | ≤ 0,35 |
| `adherence` | 100·média de `s_i`, com `s_i=max(0,1−\|ln(v/alvo)\|/ln(1+tol))`, sobre ritmo, CV, cortes/min, micro/min, punch/min, SFX/min, texto/min, meme/min e L1 da mistura | ≥ 70 |

**Índice "Cara de IA" (0 a 100; mais alto = mais cara de IA).** Pesos:

| Componente | Peso |
|---|---|
| `ai_screen_share` | 20 |
| `ai_window_share` | 10 |
| `shot_cv` | 15 |
| `kb_share` | 15 |
| `events_per_min` | 15 |
| `sfx_per_min` | 10 |
| `switch_per_min` | 10 |
| `repeat_hits` | 5 |

Cada componente vale de 0 a 1 conforme a distância até o alvo. Faixas: verde até 30, amarelo de 31 a 55, vermelho acima disso.

**Distribuição do material no tempo (revisão pós-QA do prj_gq4krrs11w).** A média escondia o bloco: IA em 17% no
vídeo e 46% num trecho de 45 s (o Golpe 2 virou um slideshow), e poucos itens do acervo voltavam em seções sem
relação. Por isso:
- o teto de IA vale também por trecho (`sources.AI_WINDOW_S` = 45 s, até `ai_window_cap` = 1,5 × `ai_max_share`; a
  folga é uma batida: com o teto igual ao da média, duas imagens de IA de 5 s nunca caberiam em 45 s). O storyboard
  corrige (`authoring._enforce_ai_time`, só nas batidas do trecho), a revisão avisa (`ai_window_over`), o índice tem o
  componente `ai_window_share` (os 30 pontos de IA viraram 20 da média + 10 do trecho) e a conferência acusa em
  `perfil.ia` com o trecho e a primeira cena de IA dele;
- cada item do acervo aparece no máximo `ITEM_MAX_USES` = 2 vezes, a `ITEM_REUSE_GAP_S` = 60 s ou mais uma da outra
  (cenas seguidas com a mesma imagem, como a foto e o cartão sobre ela, são uma aparição). A identidade é o item, o
  arquivo baixado e o endereço da imagem (`sources.media_tokens`): dois itens da mesma matéria que baixam a mesma capa
  são a mesma imagem. O anexo (`media_pool.attach_chosen`) troca o que passa disso por outro item real da reserva (sem
  nenhum, fica com aviso) e não deixa a imagem emendar noutra aparição além do limite de mesma imagem; o planejador
  segue a mesma regra nos subplanos do acervo e, quando a mesma imagem vem de cenas anteriores e passa do limite,
  troca para o acervo mesmo sem cota de `switch`. A retomada de propósito é a que o usuário fixou (cena travada,
  mídia ou subplano escolhidos à mão): conta como aparição e nunca troca.

**Onde aparece na tela**
1. **Revisão do storyboard**, na lista de achados de `storyboard_check` (contagens em `:235`):
   - selo com o índice, já antes de gastar cota;
   - lista por cena de `faltou_real`, `hook_not_real` e `ai_over_budget`.
2. **Prévia do projeto**, com `EditScore.tsx`:
   - índice e detalhamento;
   - faixa horizontal da timeline colorida por `prov` (onde está a IA no tempo);
   - botões "Reeditar (intensidade)" e "Trocar IA por real nas cenas X", que abre o Acervo.
3. **Conferência final**, no `QualityReport kind="edit"`: itens `perfil.*` com `at_s` clicável, auditados no MP4.
4. **Página da referência** (`ReferenceProfile`): perfil medido e aderência de cada projeto que usa o estilo.
5. **CLI** `studio edit-metrics`: linha de base e comparação antes e depois nos 4 projetos GTA.
