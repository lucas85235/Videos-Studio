# ADR 0002: Gestão de canal (onda 1): métricas e experimentos, Shorts do longo, operação do canal, ideias e dinheiro

- **Status:** aceita
- **Data:** 15/11/2026
- **Contexto:** a produção do Vulto está resolvida (roteiro por segmentos, conferência das cenas, narração conferida pelo
  Whisper, render por blocos, envio ao YouTube). Falta **gestão de canal**: saber o que funcionou, decidir o que fazer em
  seguida e cuidar do dinheiro. O projeto antigo `C:\Users\lucas\canais` (scripts Python soltos) já tem isso calibrado
  em uso real: `estudio/estudio.py` (tabelas `ideias`, `execucoes`, `metricas`, `experimentos`, `licoes`, `pendencias`,
  `receita`), `estudio/melhoria.py` (leitura de experimentos e resultados por variável), `estudio/rotinas.py` (backup,
  resumo da manhã), `motor/passos/shorts.py` (Shorts cortados do episódio) e o `canal.toml` de cada canal.
  Esta onda traz quatro blocos: **(A)** métricas, experimentos e lições; **(B)** Shorts cortados do longo;
  **(C)** gestão do canal (campos do canal.toml, pendências e resumo do dia, rotinas automáticas, tempo por etapa);
  **(D)** banco de ideias e monetização (ofertas de afiliado, links na descrição, receita).
  Ficam para outra onda: radar de nichos, checagem de fatos, conferência de áudio/vídeo final, provas na tela, A/B de
  miniaturas. Não vêm: GeradorMG, scripts por episódio, Kanban/perfis do Hermes, central.html.

## O que já existe no Vulto e não será duplicado

| Pedido | Já existe | Decisão |
|---|---|---|
| Horários do canal | `Channel.settings["posting"]` (`workers/publish/schedule.py`: `PostingPlan` com fuso e `Slot` semanal), página Agenda, próximo horário livre | Só ganha o **motivo** por horário (`Slot.reason`). Nada de lista nova de horários. |
| Voz de revisão × voz final | Estilo: `voice.preview` (voz local Kokoro, grátis) e `voice.voice`/`voice.chain` (final); o projeto escolhe a voz final ao ser criado (`service.create_project(voice=…)`); o build final recusa voz de teste | Sem voz nova no modelo de estilo. O canal guarda só a **voz final padrão** (`Channel.voice_defaults`), que a tela Novo projeto pré-seleciona. |
| Custo | `CostEntry` (US$ por provedor/job) e `GET /api/projects/{id}/plan` (caracteres a gravar) | `credits_per_char` do canal transforma os caracteres do plano em **créditos estimados** antes de renderizar. |
| Tempo por etapa (`execucoes`) | `Job` tem `kind`, `project_id`, `status`, `started_at`, `finished_at`; `CostEntry.job_id` | **Sem tabela nova**: tempo, falhas e retrabalho saem de `Job` (+ custo de `CostEntry`). Etapas fora do app (revisão humana) ficam para depois. |
| Envio ao YouTube e OAuth | `workers/publish/youtube.py` (token por canal, `access_token`, `_get`, `new_client`) | A coleta de métricas usa o mesmo token. O escopo do YouTube Analytics entra na lista de escopos; tokens antigos sem ele continuam publicando e coletam só o que a Data API dá. |
| Publicação | `Publication` (`external_id` = id do vídeo no YouTube, `scheduled_at`, `status`) | É a ponte vídeo ↔ projeto para as métricas. |
| Pacote/descrição | `publish_tasks._description`, `package.caption_for`, `credits.credits_block` | Os links de afiliado entram nesses dois pontos (topo da descrição + aviso legal no fim). |

## Decisões

1. **Tabelas novas pelo `create_all`, colunas novas pelo `add_missing_columns`** (sem Alembic). IDs de texto com
   `studio.ids.new_id(<prefixo>)`, como o resto do banco. Datas com `UTCDateTime`. Nomes em inglês no banco (padrão do
   `models.py`), valores de status em português (padrão dos dados do canais).
2. **Um pacote de backend por bloco**, com o próprio router e o próprio módulo de jobs:
   `workers/analytics` (A), `workers/shorts` (B), `workers/ops` (C), `workers/business` (D);
   `api/routes_analytics.py`, `routes_shorts.py`, `routes_ops.py`, `routes_business.py`.
3. **Contratos entre blocos só por banco ou por nome de job**, nunca por import de módulo de outro bloco:
   - C dispara a coleta de métricas submetendo o job `"metrics_collect"` (registrado por A) se ele existir em
     `studio.workers.jobs._REGISTRY`;
   - A lê a nota da ideia em `Idea.score` (via `Idea.project_id`) e o tipo do Short em
     `Project.settings["short_of"]["tipo"]` (escrito por B);
   - B lê a hashtag do canal em `Channel.short_hashtag` (editada na tela do C);
   - C lê `AffiliateOffer` e `RevenueEntry` (tabelas do D) para as pendências.
4. **Shorts do longo = projeto derivado** (não um corte de MP4 com composição nova): um `Project` filho (9:16) com cópia
   dos segmentos escolhidos (mesmo id e texto) e das cenas deles, imagens **fixadas** no arquivo que o longo já usou
   (`visual.asset`) e as mesmas sobreposições de voz do longo. Como a narração é endereçada pelo hash das entradas
   (texto, idioma, voz, estilo de fala, velocidade, cadeia), o filho **acha a narração no cache: nenhuma voz nova**; como
   as imagens estão fixadas, **nenhuma imagem nova**. O render, as legendas, a trilha e a publicação são os de sempre.
   O corte é por **segmento** (1 a 3 frases), a menor unidade com narração própria.
5. **Rotinas automáticas num laço dentro do processo** (ADR 0001: sem cron externo): uma thread daemon iniciada no
   `lifespan` quando `settings.workers_enabled` (os testes desligam). Cada rotina vira um job (`"routine_morning"`,
   `"backup"`) para aparecer no painel de tarefas.
6. **Lançamento à mão sempre possível**: métricas (`POST /api/analytics/metrics`) e receita (`POST /api/revenue`).
   Coleta automática nunca sobrescreve um número com vazio (upsert com `COALESCE`).

## Modelo de dados

### Colunas novas

`channels` (`Channel`), todas com padrão simples para as linhas antigas:

| Coluna | Tipo | Padrão | Origem no canal.toml |
|---|---|---|---|
| `audience` | `Text` | `""` | `publico` |
| `cadence` | `Text` | `""` | `[agenda] cadencia` (texto livre) |
| `posts_per_day` | `Integer` | `0` (sem meta) | `[agenda] posts_por_dia` |
| `min_stock` | `Integer` | `0` (sem alerta) | `[agenda] estoque_minimo` |
| `stock_format` | `String(10)` | `"short"` (`short`/`long`/`any`) | `[agenda] estoque_formato` |
| `short_hashtag` | `String(60)` | `""` | `[motor] shorts.hashtag` |
| `credits_per_char` | `Float` | `0.0` (sem estimativa) | `[voz] creditos_por_caractere` |
| `credit_price_usd` | `Float` | `0.0` (só créditos) | (novo) US$ por crédito, opcional |
| `voice_defaults` | `JSON` | `{}` | `[voz] final`: `{"final": {"provider": str, "voice": str}}` |

`projects` (`Project`): `parent_id: String(32), nullable, index=True` (projeto longo de onde o Short foi cortado; sem FK,
para o `ALTER TABLE` do SQLite ficar simples).

`Channel.settings` (JSON, sem coluna): `"posting"` (já existe; cada slot ganha `"reason": str`),
`"monetization"` (D, ver abaixo), `"shorts"` (B, limites opcionais), `"analytics"` (A, estado da coleta).

`Project.settings`: `"short_of"` (B), `"shorts_suggestions"` (B, no longo), `"affiliate"` (D).

### Tabelas novas

```python
class VideoMetric(Base):            # A — origem: tabela `metricas` (estudio/estudio.py)
    __tablename__ = "video_metrics"
    __table_args__ = (UniqueConstraint("youtube_id", "day"),)
    id: int  (Integer, pk, autoincrement)
    youtube_id: str          String(32), index
    day: str                 String(10)  "AAAA-MM-DD" (uma linha por vídeo por dia)
    publication_id: str|None String(32), index, nullable
    project_id: str|None     String(32), index, nullable
    channel_id: str|None     String(32), index, nullable
    age_hours: float|None    Float  (horas desde que foi ao ar; agendado conta da hora em que saiu)
    views, likes, comments, impressions: int|None   Integer
    ctr: float|None          Float  (% 0–100)
    avg_view_s: float|None   Float  (duração média vista, s)
    pct_watched: float|None  Float  (% médio assistido, 0–100)
    subscribers_gained: int|None
    revenue_usd: float|None; rpm: float|None
    source: str              String(20) "youtube_api" | "youtube_analytics" | "manual" | "public"
    created_at, updated_at   UTCDateTime

class Experiment(Base):             # A — origem: `experimentos`
    __tablename__ = "experiments"
    id: str                  String(32) pk, new_id("exp")
    channel_id: str|None     ForeignKey("channels.id", ondelete="SET NULL"), index
    hypothesis: str          Text
    variable: str            String(60)  (nome de VARIABLES em A, ex.: "hora", "faixa_duracao", "tipo")
    variants: list[str]      JSON default []
    metric: str              String(30) default "views" (views|views_dia|ctr|retencao|avd_s|likes)
    project_ids: list[str]   JSON default [] (vazio = todos os vídeos publicados do canal)
    status: str              String(20) default "aberto" (aberto|concluido|abandonado)
    result: str              Text default ""
    learning: str            Text default ""
    created_at; closed_at: datetime|None

class Lesson(Base):                 # A — origem: `licoes`
    __tablename__ = "lessons"
    id: str                  String(32) pk, new_id("lic")
    scope: str               String(120) ("global" | "canal:<id>" | "estilo:<id>" | "formato:short" | "formato:long")
    text: str                Text
    evidence: str            Text default ""
    origin: str              String(40) default "manual" (manual|experimento|metricas|retro)
    experiment_id: str|None  String(32), nullable
    project_id: str|None     String(32), nullable
    status: str              String(20) default "proposta" (proposta|aplicada|descartada)
    applied_where: str       Text default ""
    reason: str              Text default ""
    created_at; decided_at: datetime|None

class PendingTask(Base):            # C — origem: `pendencias`
    __tablename__ = "pending_tasks"
    id: str                  String(32) pk, new_id("pen")
    channel_id: str|None     ForeignKey("channels.id", ondelete="SET NULL"), index
    text: str                Text
    where_hint: str          Text default "" (campo "where" na API; onde fazer: link ou caminho no app, ex. "/dinheiro")
    kind: str                String(20) default "manual" (manual|auto)
    auto_key: str|None       String(120), unique, nullable (pendência criada por rotina: não duplica)
    status: str              String(20) default "aberta" (aberta|feita|descartada)
    order: int               Integer default 0
    created_at; done_at: datetime|None

class Idea(Base):                   # D — origem: `ideias`
    __tablename__ = "ideas"
    id: str                  String(32) pk, new_id("ide")
    channel_id: str|None     ForeignKey("channels.id", ondelete="SET NULL"), index
    title: str               String(300)
    angle: str               Text default ""
    format: str              String(20) default "" ("short"|"long"|"")
    origin: str              String(60) default "manual"
    evidence: str            Text default ""
    search: str              Text default "" (busca que trouxe a ideia)
    score: float|None        Float (0–10)
    status: str              String(20) default "nova" (nova|aprovada|rejeitada|em_producao|feita|descartada)
    reason: str              Text default ""
    project_id: str|None     String(32), index, nullable
    created_at; decided_at: datetime|None

class AffiliateOffer(Base):         # D — origem: [[monetizacao.oferta]] do canal.toml
    __tablename__ = "affiliate_offers"
    __table_args__ = (UniqueConstraint("channel_id", "slug"),)
    id: str                  String(32) pk, new_id("ofe")
    channel_id: str          ForeignKey("channels.id", ondelete="CASCADE"), index
    slug: str                String(60)  (id legível, ex. "gta6-ps5")
    name: str                String(200)
    store: str               String(40) default "amazon"
    asin: str                String(20) default ""
    link: str                Text default ""
    text: str                Text default "" (linha da descrição, ex. "🎮 GTA 6 em caixa (PS5)…")
    topics: list[str]        JSON default [] ("sempre" entra em todo vídeo)
    status: str              String(20) default "ideia" (ideia|ativa|pausada|descartada)
    checked_on: str          String(10) default "" (AAAA-MM-DD: última conferência de vendedor/estoque/preço)
    note: str                Text default ""
    created_at, updated_at

class RevenueEntry(Base):           # D — origem: `receita`
    __tablename__ = "revenue_entries"
    __table_args__ = (UniqueConstraint("channel_id", "store", "period", "tracking_id"),)
    id: int                  Integer pk autoincrement
    channel_id: str          ForeignKey("channels.id", ondelete="CASCADE"), index
    store: str               String(40)  (fonte: amazon, mercadolivre, adsense…)
    period: str              String(7)   "AAAA-MM"
    tracking_id: str         String(60) default "" ("" = linha de total da loja)
    clicks: int|None; orders: int|None; sales: float|None; commission: float|None
    currency: str            String(3) default "BRL"
    note: str                Text default ""
    created_at, updated_at
```

## Endpoints, jobs e páginas por bloco

Resumo (o detalhe, as regras e os testes estão no brief de cada bloco, mais abaixo):

| Bloco | Router | Jobs | Páginas web |
|---|---|---|---|
| A | `/api/analytics/*` | `metrics_collect` | `/desempenho` (Desempenho) |
| B | `/api/projects/{id}/shorts*`, `/api/shorts/check` | `shorts_suggest` | painel "Shorts deste vídeo" na aba Vídeo final do projeto |
| C | `/api/today`, `/api/pending*`, `/api/ops/*` | `routine_morning`, `backup` | `/hoje` (Hoje), campos novos em Canais, estimativa de créditos na aba de áudio, voz padrão no Novo projeto |
| D | `/api/ideas*`, `/api/channels/{id}/monetization`, `/api/channels/{id}/offers*`, `/api/projects/{id}/affiliate`, `/api/revenue*`, `/api/money/summary` | — | `/ideias` (Ideias), `/dinheiro` (Dinheiro), painel "Links de afiliado" na aba Vídeo final |

Navegação (barra lateral): **Produção**: Hoje, Projetos, Ideias, Agenda · **Gestão** (grupo novo): Desempenho, Dinheiro ·
**Biblioteca**: Estilos, Canais.

## Divisão de arquivos

A fundação roda primeiro, sozinha. Depois A, B, C e D rodam em paralelo; cada arquivo tem um dono só.

### Fundação

- `backend/studio/models.py`: as 7 classes novas, as colunas novas do `Channel` e `Project.parent_id`.
- `backend/studio/api/routes_misc.py`: `ChannelBody` e `_channel_dict` com os campos novos do canal.
- `backend/studio/workers/publish/schedule.py`: `Slot.reason: str = ""` (até 120 caracteres).
- `backend/studio/core/project/service.py` e `backend/studio/api/routes_projects.py`: `parent_id` em `get_project_row`
  e `list_projects` (só leitura).
- `backend/studio/api/app.py`: inclui os 4 routers novos; no `lifespan`, `start_routines()` depois do `recover()` e
  `stop_routines()` no fim (importados de `studio.workers.ops.routines`).
- `backend/studio/workers/jobs.py`: na última linha, importa também `studio.workers.analytics.tasks`,
  `studio.workers.shorts.tasks`, `studio.workers.ops.tasks` (registram os jobs).
- Stubs: `api/routes_analytics.py`, `api/routes_shorts.py`, `api/routes_ops.py`, `api/routes_business.py` (só
  `router = APIRouter(prefix="/api", tags=[...])`); `workers/analytics/__init__.py`, `workers/analytics/tasks.py`,
  `workers/shorts/__init__.py`, `workers/shorts/tasks.py`, `workers/ops/__init__.py`, `workers/ops/tasks.py`,
  `workers/ops/routines.py` (`start_routines()`/`stop_routines()` sem efeito), `workers/business/__init__.py`.
- `backend/tests/test_gestao_fundacao.py`.
- Web: `App.tsx` (rotas `/hoje`, `/ideias`, `/desempenho`, `/dinheiro`), `shell/Sidebar.tsx` (itens e grupo Gestão),
  `api.ts` (tipos `Channel` e `PostingSlot` ampliados, `BuildPlan.credits_estimate`, `ProjectRow.parent_id`, rótulos dos
  jobs novos), clientes `apiAnalytics.ts`, `apiShorts.ts`, `apiOps.ts`, `apiBusiness.ts` (tipos e funções do contrato
  abaixo), páginas stub `pages/Analytics.tsx`, `pages/Today.tsx`, `pages/Ideas.tsx`, `pages/Money.tsx`, componentes stub
  `pages/project/delivery/ShortsFromLong.tsx` e `pages/project/delivery/AffiliateLinks.tsx` e a montagem deles em
  `pages/project/OutputTab.tsx`.

### A: métricas, experimentos e lições
`backend/studio/workers/analytics/{metrics,variables,experiments,collect,tasks}.py`, `backend/studio/api/routes_analytics.py`,
`backend/studio/workers/publish/youtube.py` (só o escopo do Analytics e, se quiser, um helper de leitura),
`backend/tests/test_analytics.py`, `apps/web/src/apiAnalytics.ts`, `apps/web/src/pages/Analytics.tsx`,
`apps/web/src/pages/analytics/*` (novos).

### B: Shorts cortados do longo
`backend/studio/workers/shorts/{rules,derive,suggest,tasks}.py`, `backend/studio/api/routes_shorts.py`,
`backend/tests/test_shorts_from_long.py`, `apps/web/src/apiShorts.ts`,
`apps/web/src/pages/project/delivery/ShortsFromLong.tsx`, `apps/web/src/pages/project/delivery/shorts/*` (novos).

### C: operação do canal
`backend/studio/workers/ops/{routines,backup,pending,today,stock,velocity,estimate,tasks}.py`,
`backend/studio/api/routes_ops.py`, `backend/studio/workers/pipeline/build.py` (só a função `plan()`),
`backend/tests/test_ops.py`, `apps/web/src/apiOps.ts`, `apps/web/src/pages/Today.tsx`, `apps/web/src/pages/today/*`
(novos), `apps/web/src/pages/Channels.tsx`, `apps/web/src/pages/NewProject.tsx`,
`apps/web/src/pages/project/delivery/AudioPanels.tsx`.

### D: ideias e dinheiro
`backend/studio/workers/business/{ideas,monetization,revenue}.py`, `backend/studio/api/routes_business.py`,
`backend/studio/workers/publish/package.py`, `backend/studio/workers/publish_tasks.py`, `backend/tests/test_business.py`,
`apps/web/src/apiBusiness.ts`, `apps/web/src/pages/Ideas.tsx`, `apps/web/src/pages/Money.tsx`,
`apps/web/src/pages/ideas/*`, `apps/web/src/pages/money/*` (novos), `apps/web/src/pages/project/delivery/AffiliateLinks.tsx`.

Ninguém nesta onda edita `README.md`, `docs/BACKLOG.md`, `docs/USO.md`, `conftest.py`, `ui.tsx` ou `shell/data.ts`:
a atualização da documentação é feita na integração, depois da suíte completa.

## Regras trazidas do canais (resumo; o brief de cada bloco tem o detalhe)

- **Experimento** (`estudio/melhoria.py`, `avaliar`): mediana da métrica por variante, só com vídeos que passaram da
  idade mínima (**72 h Short, 168 h longo**); vencedor só se **todas** as variantes têm **3+ vídeos** e a primeira tem
  **1,3x ou mais** a mediana da segunda; senão "sem diferença clara (menos de 1,3x)" ou "cedo: precisa de 3+ vídeos por
  variante na idade mínima (faltam: …)".
- **Variáveis dos Shorts** entram sozinhas (`faixas`): hora (madrugada 0–6, manhã 6–12, tarde 12–18, noite 18–24, no
  fuso do canal), dia (dia útil/fim de semana), faixa de duração (até 35 s / 35 a 45 s / mais de 45 s), faixa de nota
  (abaixo de 7,5 / 7,5 a 8 / 8+), tipo, origem.
- **Shorts** (`motor/passos/shorts.py`, diagnóstico de 14/11/2026): 15 a 30 s, teto 35 s (45 s para lista/iceberg),
  título com até 50 caracteres antes das hashtags, hashtag do canal no fim do título, avisos de fonte no título e de
  gancho gasto; limite duro de 3 min.
- **Rotinas** (`estudio/rotinas.py`): backup diário com 14 cópias, estoque abaixo do mínimo, pedido dos números das
  lojas nos dias 1 a 10, marcos dos longos (48 h, 7 dias, 28 dias, janela de 72 h), retenção vazia (não 0) quando o
  Analytics ainda não chegou, privado/agendado sem views fora da conta.
- **Dinheiro** (`estudio/estudio.py`, seção dinheiro): link da Amazon com ID de rastreamento por vídeo, no máximo 3
  links, texto `#publi`, aviso do Programa de Associados no fim, oferta conferida há mais de 3 dias pede nova
  conferência; receita do mês soma as linhas por ID de rastreamento e ignora a linha de total.

## Consequências

- O Vulto passa a ter um ciclo completo: ideia (nota, ângulo, evidência) → projeto → vídeo → Shorts do longo →
  publicação com links → métricas → experimento → lição.
- Tokens do YouTube vinculados antes desta onda não têm o escopo do Analytics: retenção e duração média ficam vazias
  até o usuário vincular de novo (a tela avisa); views, likes e comentários vêm da Data API com o token atual.
- Lições ainda não entram sozinhas no roteiro (exigiria mexer em `workers/pipeline/authoring.py`): fica para a onda 2.
- Etapas fora do app (revisão humana, pesquisa) não entram no tempo por etapa; uma tabela de execuções manuais pode vir
  depois, se fizer falta.

---

## Apêndice: briefs de execução

### Fundação

1. `models.py`: as classes `VideoMetric`, `Experiment`, `Lesson`, `PendingTask`, `Idea`, `AffiliateOffer`,
   `RevenueEntry` exatamente como em "Modelo de dados"; colunas novas do `Channel` (`audience`, `cadence`,
   `posts_per_day`, `min_stock`, `stock_format`, `short_hashtag`, `credits_per_char`, `credit_price_usd`,
   `voice_defaults`) e `Project.parent_id`. O "onde fazer" da pendência é a coluna `where_hint` (`where` é palavra
   reservada no SQL); na API o campo se chama `where`.
2. `routes_misc.py`: `ChannelBody` ganha os campos (com limites: `posts_per_day` 0–20, `min_stock` 0–50,
   `stock_format` em `short|long|any`, `short_hashtag` vazio ou começando com `#` sem espaço, até 60, `credits_per_char`
   ≥ 0, `credit_price_usd` ≥ 0, `voice_defaults` dict); `_channel_dict` devolve todos. O `PUT` continua aplicando só os
   campos enviados (`model_fields_set`).
3. `schedule.py`: `Slot.reason: str = Field(default="", max_length=120)` (o `plan_of` antigo continua lendo slots sem
   motivo).
4. `service.py`/`routes_projects.py`: `parent_id` em `get_project_row` e `list_projects`.
5. `app.py`, `jobs.py`, stubs de routers e pacotes como em "Divisão de arquivos". `start_routines()` só faz algo quando
   `get_settings().workers_enabled` (os testes usam `workers_enabled=False`).
6. Web: rotas e itens da barra (ícones lucide: `Sun` Hoje, `Lightbulb` Ideias, `ChartLine`/`BarChart3` Desempenho,
   `Wallet` Dinheiro); páginas stub com `Page` + `PageHeader` + `Empty` ("Em construção"); `api.ts`:
   `PostingSlot.reason?: string`, campos novos opcionais no `Channel`, `BuildPlan.credits_estimate?: {credits: number;
   per_char: number; usd: number | null} | null`, `ProjectRow.parent_id?: string | null`, `JOB_LABELS` com
   `metrics_collect: 'Coleta de métricas'`, `shorts_suggest: 'Sugestão de Shorts'`, `routine_morning: 'Rotina da manhã'`,
   `backup: 'Cópia do banco'`. Clientes `apiAnalytics.ts`, `apiShorts.ts`, `apiOps.ts`, `apiBusiness.ts` importando
   `api` de `./api`, com os tipos e funções dos endpoints de cada bloco. `OutputTab.tsx` monta
   `<ShortsFromLong detail={detail} />` e `<AffiliateLinks detail={detail} lang={lang} />` (stubs que devolvem `null`).
7. Teste `test_gestao_fundacao.py`: tabelas criadas; banco antigo sem as colunas novas ganha as colunas com os padrões
   (criar a tabela `channels` à mão sem elas, rodar `init_db`); canal com os campos novos ida e volta pela API; slot com
   motivo ida e volta; os 4 routers montados (o app sobe). Critério: `uv run pytest -q -p no:warnings
   tests/test_gestao_fundacao.py tests/test_api.py tests/test_schedule.py tests/test_db.py` e `npm run typecheck` passam.

### A: métricas, experimentos e lições

Origem: `C:\Users\lucas\canais\estudio\melhoria.py` (`ultimas_metricas`, `faixas`, `resultados`, `avaliar`,
`sugestoes`, `spearman`), `estudio\estudio.py` (`cmd_metricas_add`, `cmd_exp_*`, `cmd_licao_*`),
`estudio\rotinas.py` (`gravar_metrica`, `importar_metricas_gmg`).

- `metrics.py`: `upsert_metric(youtube_id, day=None, source=..., **campos)` com COALESCE (coleta nunca apaga número);
  liga `publication_id`/`project_id`/`channel_id` pela `Publication.external_id`; `latest_metrics()` (última linha por
  vídeo); `age_hours` calculado do `publishedAt` (agendado: da hora em que saiu).
- `variables.py`: `VARIABLES` com rótulos; `video_variables(...)` devolve `formato` (`short` se o estilo do projeto tem
  `format.type == "short"`, ou duração <= 180 s), `hora` (madrugada 0-6h, manhã 6-12h, tarde 12-18h, noite 18-24h) e
  `dia` (dia útil / fim de semana) no fuso do `PostingPlan` do canal, `faixa_duracao` (< 35 "até 35 s"; <= 45 "35 a 45 s";
  senão "mais de 45 s"; duração do vídeo final em `settings.last_final` ou `contentDetails.duration`), `faixa_nota`
  (`Idea.score` pela `Idea.project_id`: < 7,5 "nota abaixo de 7,5"; < 8 "nota 7,5 a 8"; senão "nota 8+"), `tipo`
  (`Project.settings["short_of"]["tipo"]`, senão "original"), `origem` ("corte do longo" se `Project.parent_id`, senão
  "original"), `estilo` (`style_id`).
- `experiments.py`: `results(channel_id=None)` = Shorts com idade >= 72 h agrupados por variável (só variáveis com 2+
  grupos; mediana por grupo, ordem decrescente), mediana geral, calibração da nota (Spearman, None com menos de 5
  pares), longos com views e dias. `evaluate(experiment)` com a regra exata de `avaliar`: idade mínima 72 h Short /
  168 h longo; métricas `views`, `views_dia` = views / max(idade_h/24, 1), `ctr`, `retencao` (= `pct_watched`), `avd_s`,
  `likes`; vencedor só quando todas as variantes têm n >= 3 e a razão da 1ª para a 2ª mediana é >= 1,3; textos
  "X na frente: 1,4x a mediana de Y", "sem diferença clara (menos de 1,3x)", "cedo: precisa de 3+ vídeos por variante na
  idade mínima (faltam: a (1), b (2))"; conta os `jovens`; `winner` só quando há vencedor. `suggestions(results,
  open_experiments)`: parte de resultados de `sugestoes` (com 8+ Shorts: grupo n >= 3 com mediana >= 1,5x a geral →
  "Resultado"; <= 0,5x → "Alerta"; calibração |rho| < 0,2 com n >= 8 → revisar critérios, com o caso do corte),
  citando o experimento aberto da variável ou sugerindo abrir um; no máximo 4.
- `collect.py`: para cada canal com token (`youtube.account_for_channel` + `access_token`), `videos.list`
  (`part=statistics,snippet,contentDetails,status`, até 50 ids por chamada) dos vídeos das `Publication` do YouTube com
  `external_id` e dos `settings["youtube"]["recent_videos"]`; privado/agendado sem views fica de fora (lição #205).
  Depois o YouTube Analytics (`https://youtubeanalytics.googleapis.com/v2/reports`, `ids=channel==MINE`,
  `dimensions=video`, `metrics=views,averageViewDuration,averageViewPercentage,subscribersGained`,
  `filters=video==id1,id2`). 401/403 por escopo → `Channel.settings["analytics"]["scope"] = "missing"` e segue só com a
  Data API; vídeo sem dado do Analytics fica com retenção VAZIA, não 0 (lição #202). Grava `last_run`. HTTP sempre via
  `youtube.new_client()` (testes com `httpx.MockTransport`, como `test_youtube.py`). Em `youtube.py`:
  `ANALYTICS_SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"` em `SCOPES` e `LINK_SCOPES` (nunca
  `youtube.readonly` em `SCOPES`: há teste). Falta do escopo nunca quebra o envio.
- `tasks.py`: `@job("metrics_collect")`, payload `{"channel_id": str | None}`.
- `routes_analytics.py`: `GET /api/analytics/overview?channel_id=`; `GET /api/analytics/videos/{youtube_id}`;
  `POST /api/analytics/metrics` (lançar à mão); `POST /api/analytics/collect` (job); `GET /api/analytics/variables`;
  `GET|POST /api/analytics/experiments`, `GET|PUT /api/analytics/experiments/{id}` (com `evaluation`),
  `POST /api/analytics/experiments/{id}/close` (`result`, `learning`, `abandon`); `GET|POST /api/analytics/lessons`,
  `POST /api/analytics/lessons/{id}/decide` (`aplicada|descartada`, `applied_where`, `reason`). Escopo da lição:
  `global`, `canal:<id>`, `estilo:<id>`, `formato:short|long`.
- Web: página Desempenho com filtro por canal, "Atualizar métricas", aviso de escopo faltando, tabela de vídeos, "O que
  funcionou", sugestões, experimentos, lições, lançar métricas à mão.
- Testes `test_analytics.py`. Pronto com `tests/test_analytics.py tests/test_youtube.py tests/test_youtube_channels.py`
  e `npm run typecheck`.

### B: Shorts cortados do longo

Origem: `C:\Users\lucas\canais\motor\passos\shorts.py` (`conferir`, `titulo_final`, `descricao`, `DUR_*`, `TITULO_MAX`,
`FONTE_TITULO`, `GANCHO_GASTO`).

- `rules.py`: `check_short(title, duration_s, tipo, limits)` → avisos, sem travar. Teto 35 s (45 s para
  lista/iceberg); acima → "{d:.1f} s passa de {teto} s"; senão, não lista e fora de 15-30 s → "{d:.0f} s fora do alvo de 15
  a 30 s"; corpo do título (sem o bloco final de hashtags) > 50 → "título com N caracteres antes das hashtags (máximo
  50)"; `FONTE_TITULO` → 'fonte no título ("…"): vai para a descrição'; `GANCHO_GASTO` → 'gancho gasto no título
  ("…")'. Acima de 180 s é erro. Limites por canal em `Channel.settings["shorts"]`. `final_title(title, hashtag)` =
  `titulo_final`; a hashtag principal sai das hashtags da descrição.
- `derive.py`: projeto derivado (ADR, decisão 4), com timeline do longo para durações e imagens, imagens fixadas,
  mesmas sobreposições de voz, `parent_id`, `settings["short_of"]`, metadados com hashtag e "Vídeo completo no canal".
- `suggest.py`/`tasks.py`: `@job("shorts_suggest")`.
- `routes_shorts.py`: `GET /api/projects/{id}/shorts`, `POST /api/projects/{id}/shorts/suggest`,
  `POST /api/projects/{id}/shorts`, `POST /api/shorts/check`.
- Web: `ShortsFromLong.tsx`. Teste-chave: `build.plan(filho)` sem narração nem imagem a gerar.

### C: operação do canal

Origem: `C:\Users\lucas\canais\estudio\rotinas.py`, `estudio\melhoria.py` (`velocidade`), `canal.toml` dos canais.

- `backup.py` (14 cópias), `pending.py` (auto_key única), `stock.py`, `today.py`, `routines.py` (thread daemon,
  `AppSetting["routines"]`), `velocity.py` (a partir de `Job`; retrabalho >= 1,5 por projeto em 3+ projetos; gargalo >=
  30%), `estimate.py` + `build.plan()` (`credits_estimate`), jobs `routine_morning` e `backup`.
- Web: Hoje, campos novos em Canais, voz padrão no Novo projeto, créditos na aba de áudio.

### D: ideias e dinheiro

Origem: `C:\Users\lucas\canais\estudio\estudio.py` (ideias e seção dinheiro), `plantao-vice/canal.toml`.

- `ideas.py`, `monetization.py` (`offer_link`, `choose_offers`, `build_links`), `revenue.py`; integração em
  `publish_tasks.py` e `package.py`.
- Web: Ideias, Dinheiro, `AffiliateLinks.tsx`.

(Os briefs completos de A, B, C e D estão na saída do arquiteto para o orquestrador; esta seção guarda o resumo.)
