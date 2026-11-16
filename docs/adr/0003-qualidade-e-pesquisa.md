# ADR 0003: Qualidade e pesquisa (onda 2): radar de nichos, checagem de fatos, conferência de áudio e do vídeo final, provas na tela e miniaturas A/B

- **Status:** aceita
- **Data:** 15/11/2026
- **Contexto:** a onda 1 (ADR 0002) trouxe a gestão de canal (Desempenho, Hoje, Ideias, Dinheiro, Shorts do longo).
  O projeto antigo `C:\Users\lucas\canais` ainda tem, calibrado em uso real, o que falta para **escolher o que fazer**
  e **não publicar com defeito**: o radar de nichos (`estudio/radar.py`, `estudio/radar/nichos.toml`,
  `estudio/radar/painel.html`), a checagem de fatos (skills `estudio/skills/estudio/estudio-checagem` e
  `factcheck-source-retrieval`, `motor/passos/check_script.py`), a conferência de áudio (`sfx_guard.py`,
  `sfx_audit.py`, `music_curate.py`, `mix_preview.py`, `music_spectro.py`, `music_beds.py`), a conferência do vídeo
  final (`check_triggers.py`, `check_render.py`, `frames.py`) e o visual (`shot_helper.py`, `shot_views.py`,
  `trailer_shots.py`, `thumbs.py`), todos em `motor/passos/`. As regras de casa estão em
  `estudio/skills/estudio/estudio-operacao/references/padrao.md` (ex.: regra 12, "música de 14 a 19 dB abaixo da voz";
  regra 13, "sem apito"; regra 10, "linhas inteiras nas páginas").
  Esta onda traz cinco blocos: **(E)** radar de nichos e vídeos fora da curva; **(F)** checagem de fatos no roteiro;
  **(G)** conferência de áudio e várias faixas por vídeo; **(H)** conferência do vídeo final antes de publicar;
  **(I)** provas na tela, planos de vídeo de referência e 3 miniaturas para o teste A/B.
  Não vêm: GeradorMG, scripts por episódio, Kanban/perfis do Hermes, `central.html`.

## O que já existe no Vulto e não será duplicado

| Pedido | Já existe | Decisão |
|---|---|---|
| Acesso à YouTube Data API | OAuth por canal em `workers/publish/youtube.py` (`linked_accounts`, `access_token`, `new_client`, `_get`); os escopos têm `youtube.force-ssl`, que já permite `search.list` | O radar usa uma **chave de API** própria (cofre: provedor `youtube_data_api`, campo `api_key`, variável `YOUTUBE_API_KEY`), como o canais; sem chave, usa o **token de um canal vinculado** (Bearer). Sem os dois, a página explica como resolver. yt-dlp não entra no radar (no canais ele só servia de reserva das métricas). |
| Banco de ideias | `Idea` (onda 1, bloco D), `origin`, `evidence`, `search` | Vídeo fora da curva e canal "Modelar" **viram `Idea`** (`origin="radar"` ou `"outlier"`). Nada de tabela nova de ideias. |
| Pendências | `PendingTask` com `auto_key` única | "Decidir os nichos" (radar) e "trocar a miniatura em 48 h" (A/B) são pendências automáticas. |
| Fontes do roteiro | `ProjectDoc.sources` (`title`, `url`, `note`), `RealHint.source_ref`, `SceneCard.source` | A checagem parte dessas fontes e grava cada afirmação em `FactClaim` (tabela nova). O documento do projeto não muda. |
| Leitura segura de páginas | `media/safe_http.py` (`check_url`, `fetch`, limite de bytes, sem rede interna), `media/link_preview.py` | A checagem baixa a página com `safe_http.fetch` e confere o trecho literal. Nada de outro cliente HTTP. |
| Manchete literal | `core/timeline.verbatim_headline` | Mantido; a checagem cobre o resto (afirmações e citações). |
| Loudness | `media/audio.py`: `measure_loudness` (LUFS, LRA, pico real), `_k_weight`, `peak_momentary_lufs`, master a −14 LUFS e −1 dBTP, `VOICE_LUFS = −16`, passa-altas da trilha, ducking | Reaproveitado por G e H. Nada de scipy: o filtro K já existe em numpy. |
| Várias faixas | `MusicCue.start_segment` e a operação `set_music` (cria uma faixa a partir de um segmento) já existem; o build usa só a **primeira** faixa ativa | G faz o build tocar **uma cama por faixa**, trocando no segmento marcado. |
| Leitura das cenas | `core/timeline.news_reading_warnings` (manchete sem tempo de leitura), `REVEAL_MIN_VISIBLE_S = 1,2`, `STAMP_MIN_VISIBLE_S = 0,8` | H acrescenta a regra do canais (2,2 s / 1,3 s) como **aviso** sobre a timeline pronta; não mexe no cálculo da timeline. |
| Bloqueio no envio | `publish_tasks.run_publish_youtube` recusa público/agendado com provedor só de prévia | H generaliza: a conferência com **erro** bloqueia público/agendado (privado sempre passa), com "publicar mesmo assim". |
| Miniatura | `media/thumbnail.make_thumbnail`, `ThumbnailSpec.variations = 3`, `ThumbnailDoc` | I gera as 3 variações a partir disso. O build continua fazendo a miniatura padrão; a escolhida das 3 vale no envio. |
| Mídia real nas cenas | `media/real_media.py` (`store_image`, `MediaRef`), aba de cenas (`RealMediaPanel.tsx`) | A captura de página com marca-texto vira uma `MediaRef` comum (imagem já marcada e recortada): **o renderer não muda**. |
| Métricas | `VideoMetric.impressions` (onda 1) | Regra do A/B lê daí a mediana de impressões dos longos do canal em 7 dias. |

## Decisões

1. **Tabelas novas pelo `create_all`** (sem Alembic, sem coluna nova em tabela antiga nesta onda). IDs de texto com
   `studio.ids.new_id(<prefixo>)`; datas com `UTCDateTime`; nomes em inglês no banco, valores de status em português.
2. **Um pacote de backend por bloco**, com router e módulo de jobs próprios:
   `workers/radar` (E), `workers/factcheck` (F), `workers/audiocheck` + `media/audio_check.py` + `media/music_curate.py`
   (G), `workers/finalcheck` (H), `workers/visual` + `media/page_capture.py` + `media/reference_video.py` +
   `media/thumb_variants.py` (I). Routers: `api/routes_radar.py`, `routes_factcheck.py`, `routes_audio.py`,
   `routes_checks.py`, `routes_visual.py`.
3. **Contratos entre blocos só por banco** (nunca import de módulo de outro bloco):
   - G grava `QualityReport(kind="audio")`; H grava `QualityReport(kind="final")` e lê o de áudio;
   - H lê `FactClaim` (de F) para o resumo da checagem;
   - F lê `PageCapture.text_path` (de I) quando a página bloqueia o download direto;
   - H (envio, em `routes_publish.py`) e I (`publish_tasks.py`) não se chamam: H decide se o envio sai; I escolhe a
     miniatura em `Project.settings["thumbs"][lang]` dentro do job de envio.
4. **Ferramentas externas opcionais, detectadas na hora** e explicadas na tela quando faltam:
   - **Navegador headless** (captura de página): Playwright para Python, **sem** entrar no `pyproject.toml` (não mexe no
     `uv.lock`). Detecção: `importlib.util.find_spec("playwright")`; abre o **Edge do Windows** (`channel="msedge"`),
     depois `channel="chrome"`, depois o Chromium do Playwright. Sem ele: "Para capturar páginas, instale o Playwright
     no ambiente do Vulto: `cd backend` e `uv pip install playwright` (usa o Edge do Windows, sem baixar navegador)."
   - **yt-dlp** (baixar vídeo de referência por link): `VULTO_YTDLP` ou `shutil.which("yt-dlp")`. Sem ele: "Para baixar
     por link, instale o yt-dlp (`winget install yt-dlp`) ou envie o arquivo do vídeo."
   - **Busca web na checagem**: só pelo Claude Code (`claude -p` com as ferramentas `WebSearch` e `WebFetch` liberadas
     **apenas** na tarefa `factcheck`). Outros LLMs: a checagem trabalha só com as fontes do roteiro e as que o LLM
     indicar, e a conferência literal do trecho continua valendo.
   Nenhuma dependência Python nova. FFmpeg, numpy e Pillow cobrem áudio, quadros, folhas de contato e espectrogramas.
5. **Avisar não é travar**: só H bloqueia, e só envio público/agendado com item de nível `erro`; o usuário pode
   confirmar "publicar mesmo assim" (`ignore_checks`). O resto (radar, checagem, áudio) informa e propõe correção.
6. **Rotina semanal do radar** no laço do processo (ADR 0001/0002): passo `radar` na rotina da manhã, uma vez por
   semana ISO, só com `AppSetting["radar"]["auto"]` ligado, acesso à API e menos de 15 ideias novas esperando
   (`LIMITE_IDEIAS = 15`, `estudio/rotinas.py`).

## Modelo de dados

Todas as tabelas novas em `backend/studio/models.py`, numa seção "Qualidade e pesquisa (ADR 0003)". Importar
`Boolean` do SQLAlchemy para os booleanos que aceitam vazio.

```python
class RadarRun(Base):               # E — origem: radar_coletas (estudio/radar.py)
    __tablename__ = "radar_runs"
    id: int                  Integer pk autoincrement
    created_at               UTCDateTime default utcnow
    source: str              String(120) default ""   ("chave própria (YOUTUBE_API_KEY)" | "token do canal <título>")
    searches: int            Integer default 0
    units: int               Integer default 0        (cota gasta: busca = 100, o resto = 1)
    videos: int              Integer default 0
    channels: int            Integer default 0
    skipped: list[str]       JSON default []          ("nicho/mercado" sem nenhuma busca; "(falhou)" no fim quando a busca falhou)
    status: str              String(20) default "ok"  (ok | falhou)
    error: str|None          Text nullable
    params: dict             JSON default {}          (dias, hit_us, hit_br, novo_meses, candidatos, orcamento, nichos, mercados)

class RadarNiche(Base):             # E — origem: radar_nichos
    __tablename__ = "radar_niches"
    __table_args__ = (UniqueConstraint("run_id", "niche", "market"),)
    id: int                  Integer pk autoincrement
    run_id: int              ForeignKey("radar_runs.id", ondelete="CASCADE"), index
    niche: str               String(60)
    market: str              String(2)    ("us" | "br")
    n: int; h100: int; h500: int; new_hit: int; best_new: int; n10k: int; best: int     Integer default 0
    med: float; p75: float; new_share: float; subs_med: float; length_med: float         Float default 0.0
                             (length_med em minutos; new_share de 0 a 1)

class RadarVideo(Base):             # E — origem: radar_videos
    __tablename__ = "radar_videos"
    __table_args__ = (UniqueConstraint("run_id", "youtube_id", "niche", "market"),)
    id: int                  Integer pk autoincrement
    run_id: int              ForeignKey("radar_runs.id", ondelete="CASCADE"), index
    youtube_id: str          String(20), index
    niche: str               String(60)
    market: str              String(2)
    title: str               Text default ""
    channel_youtube_id: str  String(32), index
    published_at             UTCDateTime nullable
    views: int               Integer default 0
    duration_s: int          Integer default 0

class RadarChannel(Base):           # E — origem: radar_canais (atualizado a cada coleta)
    __tablename__ = "radar_channels"
    youtube_id: str          String(32) pk
    name: str                String(300) default ""
    url: str                 Text default ""
    country: str|None        String(8) nullable
    created_on: str|None     String(10) nullable  (AAAA-MM-DD)
    subscribers: int|None    Integer nullable     (canal que esconde inscritos)
    views: int               Integer default 0
    videos: int              Integer default 0
    recent: dict|None        JSON nullable        (saída de `recentes`: latest, med, hits, settled, up30, typLen, verdict, format, risk)
    alert: str|None          Text nullable
    updated_at               UTCDateTime default utcnow onupdate utcnow

class FactClaim(Base):              # F — origem: itens de roteiro/checagem-<slug>.json (skill estudio-checagem)
    __tablename__ = "fact_claims"
    id: str                  String(32) pk, new_id("fat")
    project_id: str          ForeignKey("projects.id", ondelete="CASCADE"), index
    run_id: str|None         String(32), index, nullable   (id do job de checagem)
    segment_id: str          String(32), index
    scene_id: str|None       String(32) nullable           (citação de cena "quote")
    lang: str                String(10) default "pt-BR"
    segment_hash: str        String(16) default ""         (sha256 do texto do segmento na checagem, 16 hex: muda → "desatualizada")
    excerpt: str             Text default ""               (trecho literal do roteiro que a afirmação cobre)
    claim: str               Text                          (afirmação)
    verdict: str             String(20) default "sem_fonte" (ok | corrigir | sem_fonte | boato)
    source_title: str        String(300) default ""
    url: str                 Text default ""
    quote: str               Text default ""               (trecho literal da fonte, 5 a 30 palavras)
    quote_found: bool|None   Boolean nullable              (None = não conferido)
    check_note: str          Text default ""               (onde achou ou por que não conferiu: "achado no PDF, página 3", "página não abriu", "PDF sem texto (parece escaneado...)"...)
    correction: str          Text default ""               (texto novo para `excerpt`)
    note: str                Text default ""
    status: str              String(20) default "aberta"   (aberta | aplicada | ignorada)
    checked_at               UTCDateTime nullable
    created_at, updated_at   UTCDateTime

class PageCapture(Base):            # I — origem: assets/web/shots.json (shot_helper.py + shot_views.py)
    __tablename__ = "page_captures"
    id: str                  String(32) pk, new_id("cap")
    project_id: str|None     String(32), index, nullable
    scene_id: str|None       String(32) nullable
    url: str                 Text
    domain: str              String(200) default ""
    title: str               String(300) default ""
    image: str               String(300) default ""   (PNG da página inteira capturada, relativo à pasta de dados)
    proof: str               String(300) default ""   (imagem final: marcada e recortada na janela; relativa)
    width: int; height: int  Integer default 0        (px do PNG, já com o DPR)
    dark: bool               default False            (página escura: marca-texto sem multiply)
    phrases: list[dict]      JSON default []          ([{"text", "rects": [[x,y,w,h] 0..1], "view": [x0,y0,x1,y1] | None, "lock": bool}])
    text_path: str           String(300) default ""   (innerText salvo, para a checagem literal)
    status: str              String(20) default "ok"  (ok | parcial | falhou)
    error: str|None          Text nullable
    created_at               UTCDateTime

class ReferenceVideo(Base):         # I — origem: assets/trailers/planos-<nome>.json (trailer_shots.py)
    __tablename__ = "reference_videos"
    id: str                  String(32) pk, new_id("ref")
    title: str               String(300)
    source_url: str          Text default ""
    path: str                String(300) default ""   (vídeo relativo à pasta de dados)
    info: dict               JSON default {}          (w, h, fps, codec, dur)
    shots: list[dict]        JSON default []          ([{"k", "a", "b", "dur"}])
    sheets: list[str]        JSON default []          (folhas JPG relativas)
    stats: dict              JSON default {}          (shots, median_s, cuts_per_min, palette: ["#rrggbb"...])
    status: str              String(20) default "novo" (novo | pronto | falhou)
    error: str|None          Text nullable
    created_at               UTCDateTime

class QualityReport(Base):          # G (kind "audio") e H (kind "final")
    __tablename__ = "quality_reports"
    id: int                  Integer pk autoincrement
    project_id: str          ForeignKey("projects.id", ondelete="CASCADE"), index
    kind: str                String(20)                (audio | final)
    lang: str                String(10)
    version: int             Integer default 0         (Project.current_version na conferência)
    source: str              String(300) default ""    (arquivo conferido, relativo: mix ou vídeo)
    status: str              String(10) default "ok"   (ok | aviso | erro: o pior dos itens)
    items: list[dict]        JSON default []           (ver "Item de conferência")
    data: dict               JSON default {}           (medidas brutas do bloco)
    contact_sheet: str       String(300) default ""    (folha de contato, relativa)
    created_at               UTCDateTime
```

**Item de conferência** (igual em G e H, e nos avisos rápidos de H):
`{"code": str, "level": "ok" | "aviso" | "erro", "title": str, "detail": str, "at_s": float | None, "scene_id": str | None}`.
`code` é estável (ex.: `"lufs"`, `"true_peak"`, `"sfx_tone"`, `"music_distance"`, `"late_text"`, `"short_duration"`,
`"factcheck_open"`) e a interface agrupa por ele.

**Sem tabela** (JSON já existente):

- `AppSetting["radar_catalog"]` (E): catálogo editado pelo usuário (mesmo formato do `nichos.toml`, em JSON); sem ele,
  vale o arquivo empacotado `backend/studio/workers/radar/nichos.toml` (cópia de `C:\Users\lucas\canais\estudio\radar\nichos.toml`).
- `AppSetting["radar"]` (E): `{"auto": bool, "last_week": "AAAA-Www" | None, "last_job": str | None}`.
- `AppSetting["music_curation"]` (G): `{"<caminho da faixa>|<tamanho>|<mtime>": {medidas de music_curate}}`.
- Cofre: provedor `youtube_data_api`, campo `api_key`, variável `YOUTUBE_API_KEY` (E).
- `Project.settings["thumbs"]` (I): `{lang: {"variants": [{"n": 1, "label": str, "scene_id": str | None, "text": str,
  "template": str, "path": str, "preview": str, "size": "3840x2160", "mb": float, "errors": [str]}], "chosen": 1,
  "spec": [{"scene_id", "text", "template"}], "sheet_big": str, "sheet_small": str, "made_at": iso,
  "ab": {"eligible": bool, "median_impressions": float | None, "n": int}}}`.
  **Contrato com o envio:** se `variants[chosen - 1].path` existe no disco, o envio ao YouTube e o pacote manual usam
  esse arquivo; senão, a miniatura padrão do build (`last_final.langs[lang].thumbnail`).
- `Project.settings["factcheck"]` (F): `{lang: {"checked": {segment_id: segment_hash}, "last_job": str, "at": iso,
  "web": bool}}` (quais segmentos já passaram pela checagem e com qual texto).
- `MusicCue.loop: bool = False` (G, campo novo no documento): "só a batida, em loop no compasso" para faixa da biblioteca.

## Endpoints, jobs e páginas por bloco

| Bloco | Router | Jobs | Páginas web |
|---|---|---|---|
| E | `/api/radar*` | `radar_collect` | `/radar` (Radar), grupo Gestão |
| F | `/api/projects/{id}/factcheck*` | `factcheck` | painel "Checagem de fatos" na aba Roteiro |
| G | `/api/library/music/curation`, `/api/library/music/curate`, `/api/library/music/spectro`, `/api/sfx/guard`, `/api/projects/{id}/audio-check` | `music_curate`, `audio_check` | faixas por segmento e "Conferir áudio" no painel Trilha da aba Prévia |
| H | `/api/projects/{id}/preflight`, trava em `POST /api/projects/{id}/publish/youtube` | `final_check` | painel "Antes de publicar" na aba Vídeo final + aviso/confirmação no envio |
| I | `/api/projects/{id}/scenes/{scene_id}/proof`, `/api/captures*`, `/api/visual/capabilities`, `/api/references*`, `/api/projects/{id}/thumbs*` | `page_capture`, `reference_shots`, `thumbs_variants` | `/referencias` (Referências), grupo Biblioteca; "Página com marca-texto" na mídia da cena; painel "Miniaturas (teste A/B)" na aba Vídeo final |

Navegação: **Produção**: Hoje, Projetos, Ideias, Agenda · **Gestão**: Radar, Desempenho, Dinheiro ·
**Biblioteca**: Estilos, Referências, Canais.

Rótulos de job (`JOB_LABELS` em `api.ts`): `radar_collect: 'Radar de nichos'`, `factcheck: 'Checagem de fatos'`,
`music_curate: 'Curadoria das trilhas'`, `audio_check: 'Conferência do áudio'`, `final_check: 'Conferência do vídeo
final'`, `page_capture: 'Captura de página'`, `reference_shots: 'Planos do vídeo de referência'`,
`thumbs_variants: 'Miniaturas para o teste A/B'`.

## Divisão de arquivos

A fundação roda primeiro, sozinha. Depois E, F, G, H e I rodam em paralelo; cada arquivo tem um dono só. Arquivo
existente tocado por um bloco é **só** dele nesta onda.

### Fundação
- `backend/studio/models.py` (as 8 tabelas), `backend/studio/api/app.py` (inclui 5 routers),
  `backend/studio/workers/jobs.py` (importa os 5 `tasks`), `backend/tests/test_qualidade_fundacao.py`.
- Stubs novos: `api/routes_radar.py`, `api/routes_factcheck.py`, `api/routes_audio.py`, `api/routes_checks.py`,
  `api/routes_visual.py` (só `router = APIRouter(prefix="/api", tags=[...])`); `workers/radar/__init__.py`,
  `workers/radar/tasks.py`, `workers/factcheck/__init__.py`, `workers/factcheck/tasks.py`,
  `workers/audiocheck/__init__.py`, `workers/audiocheck/tasks.py`, `workers/finalcheck/__init__.py`,
  `workers/finalcheck/tasks.py`, `workers/visual/__init__.py`, `workers/visual/tasks.py` (docstring, sem job).
- Web: `App.tsx` (rotas `/radar`, `/referencias`), `shell/Sidebar.tsx`, `api.ts` (só `JOB_LABELS`), clientes
  `apiRadar.ts`, `apiFactcheck.ts`, `apiAudio.ts`, `apiChecks.ts`, `apiVisual.ts`; páginas stub `pages/Radar.tsx`,
  `pages/References.tsx`; componentes stub (devolvem `null`) e a montagem deles:
  `pages/project/script/FactCheckPanel.tsx` em `ScriptTab.tsx`,
  `pages/project/delivery/audio/AudioCheckPanel.tsx` em `PreviewTab.tsx` (painel "trilha", abaixo do `MusicPanel`),
  `pages/project/delivery/PreflightPanel.tsx` e `pages/project/delivery/ThumbVariants.tsx` em `OutputTab.tsx`.

### E: radar de nichos
`backend/studio/workers/radar/{catalog,nichos.toml,access,analysis,collect,panel,ideas,tasks}.py|toml`,
`backend/studio/api/routes_radar.py`, `backend/studio/workers/ops/routines.py` (só o passo `radar`),
`backend/tests/test_radar.py`, `apps/web/src/apiRadar.ts`, `apps/web/src/pages/Radar.tsx`, `apps/web/src/pages/radar/*`.

### F: checagem de fatos
`backend/studio/workers/factcheck/{claims,verify,textify,apply,tasks}.py`, `backend/studio/api/routes_factcheck.py`,
`backend/studio/providers/llm.py` (só as ferramentas web da tarefa `factcheck`),
`backend/studio/workers/pipeline/mock_content.py` (só `mock_factcheck`), `backend/tests/test_factcheck.py`,
`apps/web/src/apiFactcheck.ts`, `apps/web/src/pages/project/ScriptTab.tsx`, `apps/web/src/pages/project/script/*`.

### G: conferência de áudio e várias faixas
`backend/studio/media/audio.py`, `backend/studio/media/music_library.py`, `backend/studio/media/audio_check.py` (novo),
`backend/studio/media/music_curate.py` (novo), `backend/studio/workers/audiocheck/{report,tasks}.py`,
`backend/studio/workers/pipeline/build.py` (só `music()`, `_library_music()`, `sfx_file()`, `mix()` e o leitor novo `music_track()`),
`backend/studio/core/project/document.py` (só `MusicCue`), `backend/studio/core/project/ops.py` (só `SetMusic`),
`backend/studio/api/routes_audio.py`, `backend/tests/test_audio_check.py`, `backend/tests/test_music_beds.py`,
`apps/web/src/apiAudio.ts`, `apps/web/src/pages/project/delivery/AudioPanels.tsx`,
`apps/web/src/pages/project/delivery/audio/*`.

### H: conferência do vídeo final
`backend/studio/workers/finalcheck/{measure,triggers,contact,quick,report,tasks}.py`, `backend/studio/api/routes_checks.py`,
`backend/studio/api/routes_publish.py`, `backend/tests/test_final_check.py`, `apps/web/src/apiChecks.ts`,
`apps/web/src/pages/project/OutputTab.tsx`, `apps/web/src/pages/project/delivery/publishing.tsx`,
`apps/web/src/pages/project/delivery/PreflightPanel.tsx`, `apps/web/src/pages/project/delivery/preflight/*`.

### I: visual (provas na tela, referência, miniaturas)
`backend/studio/media/page_capture.py`, `backend/studio/media/reference_video.py`, `backend/studio/media/thumb_variants.py`
(novos), `backend/studio/media/thumbnail.py`, `backend/studio/workers/visual/{proofs,references,thumbs,tasks}.py`,
`backend/studio/api/routes_visual.py`, `backend/studio/workers/publish_tasks.py`, `backend/studio/workers/publish/package.py`,
`backend/tests/test_visual.py`, `apps/web/src/apiVisual.ts`, `apps/web/src/pages/References.tsx`,
`apps/web/src/pages/references/*`, `apps/web/src/pages/project/RealMediaPanel.tsx`, `apps/web/src/pages/project/proof/*`,
`apps/web/src/pages/project/delivery/ThumbVariants.tsx`, `apps/web/src/pages/project/delivery/thumbs/*`.

Ninguém nesta onda edita `README.md`, `docs/BACKLOG.md`, `docs/DEVLOG.md`, `docs/USO.md`, `conftest.py`, `ui.tsx`,
`shell/data.ts`, `pyproject.toml` ou `uv.lock`: documentação e backlog são atualizados na integração.

## Regras trazidas do canais (resumo; o brief de cada bloco tem o detalhe)

- **Radar** (`estudio/radar.py`, `nichos.toml`): um termo por busca (o OR `|` da API devolve vídeos minúsculos),
  orçamento 3.500 unidades (busca 100, resto 1), últimos 30 dias, sem Shorts (< 180 s fora), só no idioma do mercado,
  hit 100 mil (EUA) / 50 mil (BR), canal novo até 12 meses, candidatos até 18 meses com hit; veredito **Modelar** (4+
  vídeos assentados, com 7+ dias, e metade ou mais acima do hit), **Observar** (2+ hits), **Estudar o viral** (1 hit);
  arbitragem EUA → Brasil "Aberto em PT" (demanda 100 mil+ e até 5 vídeos de 10 mil+ em PT), "Concorrido em PT" (15+),
  "Morno"; score `log10(demanda) × (1 − min(oferta, 20)/20) × 10`; fora da curva = views ÷ max(inscritos, 1.000);
  canais do sul da Ásia e com palavra de alerta ficam marcados e fora das contas.
- **Checagem** (skills e `check_script.py`): veredito `ok | corrigir | sem_fonte | boato`; citação literal de 5 a 30
  palavras, conferida na página salva com aspas e traços normalizados; nenhum `sem_fonte` sobra no roteiro final;
  checagem antes de gastar com voz paga.
- **Áudio** (`sfx_guard.py`, `sfx_audit.py`, `music_curate.py`, `music_beds.py`, `mix_preview.py`, `padrao.md`): apito =
  pico estreito entre 1,2 e 9 kHz com 18 dB de destaque por 60 ms ou mais; tom puro = 30%+ da energia em ±60 Hz acima de
  1 kHz; trilha só no trecho de batida (pouca energia em 1 a 4 kHz), em loop no compasso, cama a −21 LUFS com corte de
  até 3 dB pela banda da voz; música de 14 a 19 dB abaixo da voz em cada capítulo.
- **Vídeo final** (`check_triggers.py`, `check_render.py`, `frames.py`): texto que aparece a menos de 2,2 s do corte (1,3 s
  para carimbo/placar/anotação); −14 LUFS e pico ≤ −1 dB; voz × só música no respiro; folha de 12 quadros reais.
- **Visual** (`shot_helper.py`, `shot_views.py`, `trailer_shots.py`, `thumbs.py`): janela 1100 × 680 com DPR 2, trecho a
  42% da altura, zoom entre 1 e 1,6 que mantém a coluna inteira e 1,4 linha de folga; corte de plano com `scene > 0,3`,
  planos de menos de 0,4 s descartados, folhas de 5 × 4 quadros de 384 × 216; 3 miniaturas, a 1ª recomendada,
  3840 × 2160 até 2 MB, conferência em 168/246/360 px; teste A/B só com mediana de 2 mil impressões em 7 dias nos longos.

## Consequências

- O Vulto passa a cobrir "o que fazer" (radar → ideia) e "posso publicar?" (checagem → áudio → vídeo final → envio).
- Uma chave da YouTube Data API passa a ser recomendada (Configurações do Google Cloud, mesmo projeto do OAuth): o radar
  gasta ~3.500 das 10.000 unidades diárias do projeto quando roda (os envios têm balde próprio).
- A captura de página e o download de referência por link dependem de ferramentas que o usuário instala à parte; sem
  elas, o resto funciona.
- **Estilo a partir de vídeo de referência** fica só com a base (planos, ritmo e paleta medidos); criar o estilo a partir
  disso vai para o backlog.
- **Lições entrando no roteiro** (pendência da onda 1) continua fora: esta onda não mexe em `authoring.py`.
- Projetos antigos não mudam de som: a faixa em loop no compasso (`MusicCue.loop`) é opcional e começa desligada;
  faixas novas criadas pela tela de várias faixas já vêm com ela ligada.

---

## Apêndice: briefs de execução

Regras comuns a todos os blocos: código e comentários em PT-BR; textos de interface em PT-BR simples; siga os padrões de
`models.py`, `api/routes_*.py`, `workers/jobs.py` (`@job("tipo")`, `ctx.progress(fração, texto)`), `ui.tsx` e dos
clientes `apiX.ts` (`request()` em `api.ts` **não** põe o prefixo `/api`: escreva o caminho inteiro); nada de serviço
externo real nos testes (use `httpx.MockTransport`, funções injetadas ou `monkeypatch`); não faça commit; não apague
arquivo que você não criou; edite só os seus arquivos (em arquivo compartilhado, `Edit` pequeno); rode só os seus testes
com `cd backend && uv run pytest -q -p no:warnings <arquivos>` e `npm run typecheck` na raiz. Nada no
`C:\Users\lucas\canais` é modificado; só lido.

### Fundação

1. `models.py`: as 8 classes de "Modelo de dados", exatamente com esses nomes, tipos, padrões e índices.
2. `app.py`: importa e inclui `routes_radar`, `routes_factcheck`, `routes_audio`, `routes_checks`, `routes_visual`
   (comentário "Qualidade e pesquisa (ADR 0003)").
3. `jobs.py`: na última linha, importa também `studio.workers.radar.tasks`, `factcheck.tasks`, `audiocheck.tasks`,
   `finalcheck.tasks`, `visual.tasks` (como os da onda 1).
4. Stubs de routers e pacotes como em "Divisão de arquivos".
5. Web: rotas `/radar` (`RadarPage`) e `/referencias` (`ReferencesPage`) com `lazy`; barra lateral: `Radar` (ícone
   lucide `Radar`) no topo de Gestão, `Referências` (ícone `Film`) entre Estilos e Canais; `JOB_LABELS` com os 8 rótulos;
   clientes `apiRadar.ts`, `apiFactcheck.ts`, `apiAudio.ts`, `apiChecks.ts`, `apiVisual.ts` importando `api` de `./api`,
   com os tipos e funções dos endpoints de cada brief (os donos ajustam depois); páginas stub com `Page` + `PageHeader` +
   `Empty` ("Em construção"); componentes stub montados: `<FactCheckPanel detail={detail} />` no fim do `ScriptTab`
   (antes da seção Fontes), `<AudioCheckPanel detail={detail} lang={lang} />` abaixo do `MusicPanel` no painel "trilha"
   do `PreviewTab`, `<PreflightPanel detail={detail} lang={lang} />` e `<ThumbVariants detail={detail} lang={lang} />` no
   `OutputTab` (o primeiro antes da seção de publicar, o segundo junto da miniatura).
6. `test_qualidade_fundacao.py`: as 8 tabelas existem depois do `init_db`; banco antigo sem elas ganha as tabelas;
   ida e volta de uma linha de cada (JSON e `Boolean` nulo inclusive); `QualityReport` e `FactClaim` somem ao apagar o
   projeto pela API (se o SQLite do app não ativa FK, teste só a criação); o app sobe com os 5 routers (`/api/health`).
   Pronto quando `tests/test_qualidade_fundacao.py tests/test_gestao_fundacao.py tests/test_api.py tests/test_db.py`
   e `npm run typecheck` passam.

### E: radar de nichos e vídeos fora da curva

**Origem (só leitura):** `C:\Users\lucas\canais\estudio\radar.py` (todas as funções), `estudio\radar\nichos.toml`,
`estudio\radar\painel.html` (abas, filtros, textos e fatos), `estudio\rotinas.py` (`radar`, `LIMITE_IDEIAS = 15`).
O canais coleta pela **YouTube Data API v3** (`YOUTUBE_API_KEY` ou o login do GeradorMG); yt-dlp não entra no radar.

**Arquivos (só seus):** `backend/studio/workers/radar/{catalog.py, nichos.toml, access.py, analysis.py, collect.py,
panel.py, ideas.py, tasks.py}` (o `__init__.py` e o `tasks.py` vazio vêm da fundação), `backend/studio/api/routes_radar.py`,
`backend/studio/workers/ops/routines.py` (só o passo novo), `backend/tests/test_radar.py`, `apps/web/src/apiRadar.ts`,
`apps/web/src/pages/Radar.tsx`, `apps/web/src/pages/radar/*` (novos). Tabelas (já criadas pela fundação): `RadarRun`,
`RadarNiche`, `RadarVideo`, `RadarChannel`; escreve também em `Idea` e `PendingTask` (onda 1).

1. **`nichos.toml`**: cópia do catálogo do canais (15 nichos, `[radar]`, `evitar`, `alertas`, `[rpm]`), trocando só o
   comentário dos envios do GeradorMG por "os envios do Vulto têm cota própria (100/dia)".
2. **`catalog.py`**: `load_catalog()` (= `AppSetting["radar_catalog"]` se existir, senão o TOML com `tomllib`),
   `validate_catalog(data)` e `save_catalog`/`reset_catalog`. Regras: `id` único em `^[a-z0-9-]{1,60}$`, `nome`, `cat`,
   `duracao` em `medium` (4 a 20 min, padrão) ou `long` (mais de 20 min), `en` e `pt` com pelo menos 1 termo, termo sem
   `|` (erro: "não use | (OR): a API devolve vídeos minúsculos; um termo por linha"). Padrões do `[radar]`:
   `orcamento = 3500`, `dias = 30`, `hit_us = 100000`, `hit_br = 50000`, `novo_meses = 12`, `candidatos = 40`.
3. **`analysis.py`** (puro, sem rede nem banco; porte fiel): `MERCADOS = {"us": {"nome": "EUA", "regiao": "US",
   "lingua": "en"}, "br": {"nome": "BR", "regiao": "BR", "lingua": "pt"}}`; `PT_PALAVRAS`, `EN_PALAVRAS`, `ACENTOS`,
   `OUTRA_ESCRITA` (mesmas regex), `SUL_ASIA = {"IN","PK","BD","NP","LK"}`; `segundos_iso`, `quando_iso`, `mediana`,
   `quantil` (interpolação linear), `fmt` ("1,2 mi", "350 mil"); `idioma_ok(snippet, mercado)` (outra escrita no título →
   fora; idioma declarado vale; sem ele, BR aceita `en <= pt` (acento conta 2) e EUA aceita `pt <= en` sem acento);
   `alertas(cfg, linhas, canais)` (sul da Ásia: "Canal do sul da Ásia (país XX): público e RPM diferentes dos
   mercados-alvo"; palavra de `alertas` no nome do canal ou em metade ou mais dos títulos dele: `2 * acertos >= n`);
   `agregar` (só canais sem alerta; hit do mercado; canal novo = idade ≤ `novo_meses`, idade desconhecida conta 999;
   campos `n`, `med`, `p75`, `h100` (views ≥ hit), `h500` (≥ 5 × hit), `new_share`, `new_hit` (canais novos distintos
   com vídeo ≥ hit), `best_new`, `subs_med` (mediana de inscritos dos canais distintos), `length_med` (mediana em min),
   `n10k` (≥ 10.000), `best`); `analisar(cfg, buscas, videos, canais, agora)` (vídeo entra se publicado há ≤ `dias`,
   duração ≥ 180 s, `idioma_ok` e canal conhecido; idade do canal em meses = dias / 30,44); `candidatos(cfg, analise,
   limite)` (canal sem alerta, ≤ 18 meses, vídeo ≥ hit; ordem pelo melhor vídeo); `recentes(itens, hit, agora)`
   (assentados = 7+ dias com views; `up30` = vídeos dos últimos 30 dias; `typLen` = mediana das durações ≥ 3 min;
   **Modelar** se assentados ≥ 4 e hits/assentados ≥ 0,5; senão **Observar** se hits ≥ 2; senão **Estudar o viral** se
   hits = 1; `format` = "{typLen} min · {up30} vídeos em 30 dias · {hits}/{assentados} com {fmt(hit)}+"; `risk` com os
   3 textos exatos do canais); `arbitragem(nichos, cfg)` (demanda = mediana EUA, oferta = `n10k` BR; "Aberto em PT" se
   demanda ≥ 100.000 e oferta ≤ 5, "Concorrido em PT" se oferta ≥ 15, senão "Morno"; `score = round(log10(max(demanda,
   1)) * (1 - min(oferta, 20) / 20) * 10, 1)`; ordem por score decrescente).
4. **`access.py`**: `resolve_access()` → chave do cofre (`credentials.resolve("youtube_data_api", "api_key",
   ("YOUTUBE_API_KEY",))`, fonte "chave própria (YOUTUBE_API_KEY)") ou, sem ela, a primeira conta de
   `youtube.linked_accounts()` com token (Bearer de `youtube.access_token(http, account)`, fonte "token do canal
   <título>"). Sem nenhum: `RadarAccessError("Sem acesso à YouTube Data API: cole uma chave de API em Radar → Acesso
   (Google Cloud → APIs e serviços → Credenciais → Criar chave de API, com a YouTube Data API v3 ativada) ou vincule um
   canal do YouTube em Canais.")`. Classe `Api(access, orcamento, http)`: custo 100 para `search?`, 1 para o resto;
   `lote(caminhos)` faz os GET em `https://www.googleapis.com/youtube/v3/<caminho>` na ordem, falha vira `None`, soma
   unidades e pedidos; se **todos** falham, `RadarAccessError("a API recusou tudo: …")` (403 `quotaExceeded` → "a cota do
   dia acabou: tente amanhã ou diminua o orçamento"). HTTP sempre por `youtube.new_client()` (injetável).
5. **`collect.py`**: `plan(cfg, nichos, mercados, orcamento)` = lógica exata do `coletar`: **um termo por busca**,
   primeiro o 1º termo de todos os nichos e mercados, depois o 2º; `reserva = 2 * candidatos + 60`;
   `cabem = max(0, (orcamento - reserva) // 100)`; `pulados` = só nicho/mercado sem nenhuma busca. Parâmetros da busca:
   `part=snippet, type=video, order=viewCount, maxResults=50, publishedAfter=<agora - dias>, q, regionCode,
   relevanceLanguage, videoDuration=<duracao do nicho>, safeSearch=none`. `simulate(...)` devolve `{buscas,
   unidades_previstas = buscas * 100 + reserva, orcamento, pulados, exemplo}` sem rede. `collect(...)`: buscas →
   `videos?part=snippet,statistics,contentDetails&id=` em lotes de 50 → `channels?part=snippet,statistics&id=` em lotes
   de 50 → `analisar` → `livres = (orcamento - unidades) // 2` → `candidatos(min(cfg.candidatos, livres))` →
   `playlistItems?part=contentDetails&maxResults=20&playlistId=UU<id[2:]>` → detalhes dos vídeos → `recentes`. Grava
   `RadarRun` (com `skipped` + "nicho/mercado (falhou)"), `RadarNiche`, `RadarVideo` e faz upsert de `RadarChannel`
   (`recent` novo só substitui quando veio; nunca apaga com vazio). `reprocess(run_id)` refaz alertas e agregados do banco
   com o catálogo atual, **sem gastar cota** (= `reprocessar`). Progresso por callback.
6. **`panel.py`**: `panel_data(run_id)` no formato de `dados_painel`: `meta` (data dd/mm/aaaa, buscas, vídeos, canais,
   descrição, `method` como lista de passos e limites com os textos do canais, `skipped`); `niches` (só os com `n` e
   `med` > 0: `name` "{nome} · {EUA|BR}", `niche`, `mk`, `q`, `n`, `med`, `p75`, `h100`, `h500`, `newShare`, `newCh`,
   `bestNew`, `subs` = max(subs_med, 1), `len`, `cat`, `rpm`, `small`); `channels` (id, name, url, country, joined, ageM,
   subs, views, videos, niches, sample, best, bestViews, `vpm = round(views / max(ageM or 1, 1))`, `flag` + campos de
   `recent`); `videos` (id, t, v, c, a em dias, l em min, n); `arb`; `recs` (por mercado, até 4 canais sem alerta com
   veredito Modelar ou Observar e nicho daquele mercado, ordem `(veredito != Modelar, -vpm)`); `avoid` (`evitar`);
   `rpm` (`[rpm]`); `facts` (os 6 itens de "Para monetizar" do `painel.html`, com os links) e `markets` (RPM por país:
   Austrália 7,08 · Noruega 6,35 · EUA 5,12 · Canadá 5,04 · Reino Unido 4,65 · Média global 1,14). `report_lines(top=4)`
   = `relatorio`.
7. **`ideas.py`**: `idea_from_video(run_id, youtube_id, channel_id)` e `idea_from_channel(run_id, channel_youtube_id,
   channel_id)` criam `Idea(title=…[:300], format="long", origin="outlier" | "radar", evidence="<views> views em <n> dias;
   canal com <inscritos> inscritos (<razão>× inscritos); <veredito>; https://youtu.be/<id>", search=<1º termo do nicho no
   mercado>, status="nova")`. Repetida (mesmo `channel_id` e mesma URL na evidência) devolve a existente com
   `created: false`. `ensure_pending(week)`: `PendingTask(kind="auto", auto_key="radar:decidir:<AAAA-Www>", text="Ler o
   radar de nichos da semana e decidir nichos e ideias", where_hint="/radar")`.
8. **`tasks.py`**: `@job("radar_collect")`, payload `{niches?: [str], markets?: ["us","br"], budget?: int}`; resultado
   `{run_id, buscas, unidades, videos, canais, pulados, falhas, fonte}`; no fim cria a pendência e grava
   `AppSetting["radar"]["last_job"]`.
9. **`routes_radar.py`** (prefixo `/api`): `GET /api/radar` (`{access: {ok, source, message}, auto, last_run, last_job,
   catalog: {niches, markets}}`); `PUT /api/radar/key {api_key}` e `DELETE /api/radar/key` (cofre, nunca devolve a chave,
   só `credentials.mask`); `GET|PUT /api/radar/catalog`, `POST /api/radar/catalog/reset`; `POST /api/radar/simulate`;
   `POST /api/radar/collect` (job; 400 com a mensagem de acesso quando não há acesso); `GET /api/radar/runs`;
   `GET /api/radar/runs/{id}/panel`; `POST /api/radar/runs/{id}/reprocess`; `GET /api/radar/report`;
   `POST /api/radar/ideas {run_id, youtube_id? | channel_youtube_id?, channel_id?}`; `PUT /api/radar/settings {auto}`.
10. **`routines.py`**: novo passo `("radar", step_radar)` no fim de `STEPS`. Submete `radar_collect` uma vez por semana
    ISO (`AppSetting["radar"]["last_week"]`) se `auto` está ligado, o job está registrado, há acesso e há menos de 15
    `Idea` com status `nova`; senão devolve a linha "radar pulado: N ideias novas esperando você". Não mexa nos outros
    passos.
11. **Web** (`Radar.tsx` + `pages/radar/*`): abas como o `painel.html`: **Visão geral** (KPIs: vídeos do último mês,
    canais lidos, canais com até 18 meses e vídeo de 100 mil+, nichos comparados, buscas; seletor EUA/BR; mapa de
    oportunidade em SVG log × log com x = mediana de inscritos, y = mediana de views, raio `6 + 1,4 × canais novos com
    hit`, cor pela % de vídeos de canais novos; "Por onde começar"; "Evitar no primeiro canal"; ranking ordenável;
    RPM por país e "Para monetizar"), **Canais** (filtros: busca, nicho, idade 3/6/12/18 (padrão)/qualquer, ordenar
    por recomendados/views por mês de vida/mediana dos recentes/% com 100 mil+/views/inscritos/mais novos, "com vídeo de
    100 mil+" ligado, "esconder alertas" ligado, "só com veredito"; barrinhas das views recentes, verde acima do hit,
    cinza-escuro com menos de 7 dias; 48 por vez e "Mostrar mais"), **Vídeos fora da curva** (nicho, idade (padrão 12),
    views mínimas 50 mil/100 mil (padrão)/500 mil/1 mi, ordenar por views ÷ inscritos (padrão; mínimo 1.000
    inscritos)/mais views/mais recentes, esconder alertas; selo "N× inscritos"), **Arbitragem EUA → Brasil** e
    **Método**. Cartão "Acesso e coleta": estado do acesso, campo da chave, "Simular" (buscas e cota), "Coletar agora",
    "Rodar toda semana", "Refazer as contas sem gastar cota" e editor do catálogo (nichos, termos EN/PT, duração,
    categoria; evitar; alertas). Botão **"Virar ideia"** nos vídeos e nos canais recomendados, com escolha do canal do
    Vulto. Miniaturas de `i.ytimg.com` com `onError` que esconde.
12. **Testes `test_radar.py`** (sem rede: `httpx.MockTransport`): `idioma_ok` (título PT, EN, com acento, outra escrita);
    `alertas` (sul da Ásia, palavra no nome, metade dos títulos); `agregar` com números conferidos à mão; `recentes` nos 3
    vereditos e sem veredito; `arbitragem` (ex.: demanda 250.000 e oferta 3 → "Aberto em PT", score 45,9); `plan` com 15
    nichos × 2 mercados × 2 termos e orçamento 3.500 (reserva 140, 33 buscas, rodízio por termo, `pulados` vazio);
    coleta simulada ponta a ponta (search, videos, channels, playlistItems) gravando as 4 tabelas e contando unidades;
    tudo falhando → erro; sem chave nem token → mensagem de acesso; `reprocess`; ideia de vídeo e repetida; passo da
    rotina submete 1 vez por semana e pula com 15 ideias novas; rotas principais. **Pronto** quando
    `tests/test_radar.py tests/test_ops.py tests/test_ops_v2.py tests/test_youtube.py` e `npm run typecheck` passam.

### F: checagem de fatos no roteiro

**Origem (só leitura):** `C:\Users\lucas\canais\estudio\skills\estudio\estudio-checagem\SKILL.md` (procedimento,
formato do item, regras, armadilhas), `estudio\skills\estudio\factcheck-source-retrieval\SKILL.md` (aspas curvas,
"verbatim gate", 5 a 30 palavras, PDF corrigido, página viva, atribuição), `motor\passos\check_script.py` (o trecho
marcado tem de estar no texto da página/citação), `motor\passos\shot_helper.py` (`norm` do `FIND_JS`),
`estudio\skills\estudio\estudio-operacao\references\padrao.md` (regras 2, 3, 3b, 4).

**Arquivos (só seus):** `backend/studio/workers/factcheck/{claims.py, verify.py, textify.py, apply.py, tasks.py}`,
`backend/studio/api/routes_factcheck.py`, `backend/studio/providers/llm.py` (só a liberação de busca web na tarefa
`factcheck`), `backend/studio/workers/pipeline/mock_content.py` (só `mock_factcheck`), `backend/tests/test_factcheck.py`,
`apps/web/src/apiFactcheck.ts`, `apps/web/src/pages/project/ScriptTab.tsx`, `apps/web/src/pages/project/script/*`.
Tabela: `FactClaim`; lê `PageCapture` (de I) e escreve `Project.settings["factcheck"]`.

1. **`textify.py`**: `html_to_text(html)` com `html.parser` (ignora `script`, `style`, `noscript`, `template`, `svg`,
   `nav`, `footer`, `header`, `aside`, `form`; quebra de linha nos blocos; `html.unescape`); `normalize(s)`: NFKC,
   `‘ ’ ʼ` → `'`, `“ ” « »` → `"`, U+2010 a U+2015 e U+2212 → `-`, espaço duro → espaço, espaços juntos, `casefold`;
   `find_quote(quote, text)` → `"found"` (substring normalizada) | `"loose"` (sem nenhum espaço dos dois lados: texto de
   OCR que junta palavras) | `"missing"`; `words(s)`.
2. **`verify.py`**: `verify(url, quote, *, http=None) -> (found: bool | None, note: str)`. Sem URL → `(None, "sem link
   da fonte")`; sem trecho → `(None, "sem trecho literal da fonte")`. Trecho fora de 5 a 30 palavras ainda é conferido,
   com a nota "trecho com N palavras (o certo é de 5 a 30)". Baixa com `safe_http.fetch(url, max_bytes=HTML_MAX_BYTES,
   accept=ACCEPT_HTML, truncate=True, http=http)`; PDF → lido página por página com o `pypdf` (`pdftext.extract`; baixado de novo inteiro até 15 MB quando o 1º
   download cortou; o mesmo PDF é lido uma vez só por URL e conteúdo): achado → `(True, "achado no PDF, página N")`
   (ou "páginas N e N+1" quando atravessa a virada, pulando cabeçalho, rodapé e número de página); não achado →
   `(False, "o trecho não está no PDF: ...")`; PDF escaneado → `(None, "PDF sem texto (parece escaneado, só imagem):
   ... confira o trecho à mão")`; PDF que não abriu ou cujas páginas não leram → `(None, "... confira o trecho à mão")`;
   PDF grande demais → `(None, "PDF grande demais (passa de 15 MB): confira o trecho à mão")`; PDF longo cortado →
   `(None, "PDF longo: só as primeiras N de M páginas foram lidas ...")`;
   HTML → `html_to_text`; texto → direto. Falha de rede/bloqueio: usa o `text_path` da `PageCapture` mais recente com a
   mesma URL, se houver; senão `(None, "a página não abriu (<motivo>): confira à mão ou capture a página na aba
   Cenas")`. `loose` → `(True, "achado ignorando os espaços (texto de OCR?)")`; `missing` → `(False, "o trecho não está
   na página: copie o texto exato da fonte (aspas curvas e retas contam)")`. Nunca chame a rede de verdade nos testes
   (MockTransport + `monkeypatch` de `safe_http.resolve` para um IP público, como em `test_real_media_fetch.py`).
3. **`claims.py`**: `FACTCHECK_SYSTEM` em PT com as regras das skills: conferir cada data, número, nome, preço e citação;
   veredito `ok | corrigir | sem_fonte | boato`; pessoa em processo é "acusada", detalhe atribuído a quem afirmou e com
   data; boato marcado (quem disse e quando), boato desmentido entra como desmentido; opinião do canal entra como opinião;
   preço e data na página oficial, no fuso do mercado; página viva guarda o "última atualização"; empresa de análise
   (Chainalysis, Elliptic) não é governo; citação em outra língua pelo veículo que ouviu no original; relatório corrigido
   pode ter número diferente do comunicado (informe os dois); nunca invente URL; `quote` copiado da página, de 5 a 30
   palavras; `excerpt` é o trecho **literal** do segmento que a afirmação cobre; `correction` reescreve só o `excerpt`.
   O pedido traz a data de hoje, o idioma, os segmentos (id, papel, capítulo, texto), as fontes do documento numeradas
   (`doc.sources`) e as cenas de citação (`kind == "quote"`, com o texto e a fonte do cartão). Esquema:
   `{"items": [{"segment_id", "scene_id"?, "excerpt", "claim", "verdict", "source_title", "url", "quote", "correction",
   "note"}]}`. Chama `authoring._llm("factcheck", …)` (cadeia de LLM de sempre, custo registrado). Item com
   `segment_id` desconhecido sai; `excerpt` que não está no segmento (normalizado) fica vazio com nota "trecho não achado
   no roteiro". Uma checagem nova de um segmento **substitui** as afirmações anteriores dele naquele idioma. Depois confere
   todos os trechos (até 4 ao mesmo tempo) e grava `quote_found`, `check_note`, `checked_at`, `segment_hash` (sha256 do
   texto do segmento, 16 hex) e `Project.settings["factcheck"][lang]`.
4. **`apply.py`**: `apply_correction(project_id, claim_id)`: o segmento tem de conter o `excerpt` (senão 409 "o trecho
   mudou desde a checagem: edite à mão"); segmento travado → 409 "segmento travado"; troca a 1ª ocorrência por
   `correction` com `UpdateSegmentText(segment_id, lang, text)` + `apply_ops` + `service.commit(project_id, doc,
   "Checagem: <afirmação[:60]>", author="factcheck")`; afirmação vira `status="aplicada"`. `summary(project_id, lang)`:
   contagem por veredito, `open` (aberta e veredito `corrigir`/`sem_fonte`/`boato`), `stale` (`segment_hash` diferente do
   texto atual ou segmento apagado), `quote_missing`, `unchecked` (segmentos sem checagem ou com texto mudado).
5. **`llm.py`**: `WEB_TASKS = frozenset({"factcheck"})`. No `ClaudeCodeLLM`, separe a montagem do comando numa função
   testável (`_command(task, schema, system_file)`); para tarefa em `WEB_TASKS` e `VS_FACTCHECK_WEB != "0"`:
   `--tools "WebSearch,WebFetch"` e `--allowedTools WebSearch WebFetch` (sem isso o `-p` para pedindo permissão) e tempo
   limite de 1.800 s; as outras tarefas continuam com `--tools ""`. `--safe-mode` e `--no-session-persistence` ficam
   sempre. `web_search_available()` diz se o 1º LLM disponível da cadeia é o Claude Code. Não mexa em mais nada.
6. **`mock_content.mock_factcheck(ctx)`**: determinístico; para cada segmento com algarismo, um item `ok` com a 1ª fonte
   do contexto e as 6 primeiras palavras do segmento como `quote`; sem algarismo, nada.
7. **`tasks.py`**: `@job("factcheck")`, payload `{lang?, segment_ids?}`; progresso "Lendo o roteiro" → "Conferindo N
   trechos nas páginas"; resultado `{claims, ok, corrigir, sem_fonte, boato, quote_missing, web}`.
8. **`routes_factcheck.py`**: `GET /api/projects/{id}/factcheck?lang=` (`{claims: [...], summary, web: {available,
   reason}, last_job}`); `POST /api/projects/{id}/factcheck {lang?, segment_ids?}` (job); `PATCH
   /api/projects/{id}/factcheck/{claim_id}` (`verdict`, `source_title`, `url`, `quote`, `correction`, `note`, `status`
   em `aberta|ignorada`); `POST …/{claim_id}/verify` (reconfere na hora); `POST …/{claim_id}/apply`;
   `GET /api/projects/{id}/factcheck/sources.md?lang=` (tabela fala → fonte, como o `fontes-<slug>.md`).
9. **Web**: `script/FactCheckPanel.tsx` (montado pela fundação no `ScriptTab`): "Checar fatos" (job), estado da busca web
   ("Busca web ligada (Claude)" ou "Sem busca web: só as fontes do roteiro e as que o modelo indicar"), resumo em
   selos, aviso "Checagem completa antes de gastar com voz paga" quando há abertas, lista por segmento com afirmação,
   veredito (ok verde, corrigir âmbar, sem fonte vermelho, boato roxo), trecho → correção, link da fonte (nova aba),
   citação com "achada / não achada / não conferida" e a nota, ações "Aplicar correção", "Reconferir", "Editar",
   "Ignorar", marca "desatualizada", e "Copiar fontes". No `ScriptTab`, selo com as abertas em cada `SegmentCard`.
10. **Testes `test_factcheck.py`**: `normalize` (aspas curvas, travessão, espaço duro); `html_to_text` sem script/nav;
    `find_quote` found/loose/missing; nota de 5 a 30 palavras; `verify` com HTML, PDF, 403 e reserva pela `PageCapture`;
    job offline (MockLLM) cria e confere as afirmações; `apply` grava versão nova e marca "aplicada"; segmento travado e
    trecho mudado → 409; desatualizada depois de editar o segmento; `_command` só libera `WebSearch`/`WebFetch` na
    `factcheck`. **Pronto** quando `tests/test_factcheck.py tests/test_claude_code.py tests/test_pipeline.py` e
    `npm run typecheck` passam.

### G: conferência de áudio e várias faixas por vídeo

**Origem (só leitura):** `C:\Users\lucas\canais\motor\passos\sfx_guard.py`, `sfx_audit.py`, `music_curate.py`,
`mix_preview.py`, `music_beds.py`, `music_spectro.py`; `estudio\skills\estudio\estudio-operacao\references\padrao.md`
(regras 12 e 13); `estudio\skills\estudio\faceless-video-pipeline\references\audio.md`.

**Arquivos (só seus):** `backend/studio/media/audio.py`, `backend/studio/media/music_library.py`,
`backend/studio/media/audio_check.py` (novo), `backend/studio/media/music_curate.py` (novo),
`backend/studio/workers/audiocheck/{report.py, tasks.py}`, `backend/studio/workers/pipeline/build.py` (só `music()`,
`_library_music()`, `sfx_file()`, `mix()` e um leitor `music_track()`), `backend/studio/core/project/document.py` (só
`MusicCue`), `backend/studio/core/project/ops.py` (só `SetMusic`), `backend/studio/api/routes_audio.py`,
`backend/tests/test_audio_check.py`, `backend/tests/test_music_beds.py`, `apps/web/src/apiAudio.ts`,
`apps/web/src/pages/project/delivery/AudioPanels.tsx`, `apps/web/src/pages/project/delivery/audio/*`. Grava
`QualityReport(kind="audio")` e `AppSetting["music_curation"]`. Sem scipy: use `audio._k_weight` e numpy.

1. **`media/audio_check.py`**:
   - `SFX_BANNED = {"whip", "whip2", "ding", "trombone"}` (ouvidos como apito no canais; nunca, mesmo que a medida passe).
   - `tone_report(x, sr=48000)` = `sfx_guard.tone_report`: faixa 1.200 a 9.000 Hz, FFT de 1.024 com salto de 256 e
     Hann, vizinhança ±6 bins (`S[k-18:k-2]` e `S[k+3:k+18]`), destaque = pico − mediana da vizinhança, quadro abaixo
     de −45 dB RMS não conta, pico "forte" = `S[k] > max(S) - 30`, tonal com destaque ≥ 18 dB; `tonal_ms = quadros ×
     256 × 1000 / sr`; **apito = tonal_ms ≥ 60**. Devolve `dur, prom, hz, tonal_ms, apito`. `tone_report_file(path)`.
   - `tonal_fraction(seg, sr)` = `sfx_audit.tonal_frac`: FFT 4.096, salto 2.048, pico acima de 1 kHz, fração da energia
     em ±60 Hz; **tom puro > 0,3**.
   - `momentary_lufs(x, sr)` (janela 400 ms, salto 100 ms, filtro K de `audio._k_weight`, `-0,691 + 10 log10`) e
     `integrated_lufs(x, sr)` (BS.1770 com portas absoluta −70 e relativa −10; até 0,5 LU do `measure_loudness`).
   - `sfx_events(sfx, voice, sr)` = `sfx_audit`: evento começa acima de −50, segue enquanto > −55; `t = a/sr + 0,2`;
     `vs_voz = pico − mediana da voz (janelas > −45)`; `tom` de 0,5 s a partir do pico.
   - `speech_mask(timeline, n, sr)`: palavras das cenas (quadros relativos ao `from` da cena) com 0,12 s antes e 0,3 s
     depois (`mix_preview`).
   - `chapter_levels(voice, music, mask, timeline, sr)`: marcas `[(0, "Abertura")]` + `timeline["chapters"]` com início
     > 0; por trecho: voz sob a fala, trilha sob a fala, trilha nas pausas, distância = voz − trilha; e a linha do vídeo
     inteiro.
   - Itens: distância **de 14 a 19 dB → ok** (padrão, regra 12); < 14 → aviso "música alta: X dB abaixo da voz (o
     padrão é de 14 a 19)"; > 19 → aviso "música baixa demais: X dB abaixo da voz"; efeito com tom > 0,3 ou apito →
     **erro** "efeito com apito"; efeito com `vs_voz` > 0 → aviso "efeito mais alto que a voz".
2. **`media/music_curate.py`** = `curate` do canais, recebendo o caminho da faixa e devolvendo as medidas e o loop:
   análise em estéreo 22.050 Hz, janela 0,5 s, `HOP_S = 0,25`, bandas `vol` 120 a 11.000 Hz, `pres` 1 a 4 kHz,
   `centro` 300 a 3.400 Hz (meio − lados), `flat` (planura 400 Hz a 3 kHz), mediana móvel de 8; `onsets` (salto 256,
   fluxo espectral log); `beat_period` entre 68 e 165 bpm com refinamento parabólico; compasso de **8 batidas se bpm >
   140, senão 4**; loop de **8 compassos** (4 se 8 passam de 45% da faixa); janelas de compasso em compasso a partir de
   1,0 s enquanto `t + L < dur − 6`; `score = pres.mean + 1,5 × (vol.std + pres.std) + 20 × flat.mean` (menor vence);
   começo alinhado ao ataque mais forte dentro de um período; tamanho do loop pela repetição da meia-frase (busca de
   ±0,3 período, NCC do fluxo); fim afinado na onda (±10 ms, janela de 80 ms, passo 4 amostras); `first_change`
   (banda da voz +6 dB ou volume +3 dB por 2 s depois da referência de 1 a 7 s). Saída com as chaves do canais: `bpm,
   loop_start_s, loop_s, loop_bars, emenda_batida, emenda_onda, voz_db, voz_vs_pico_db, variacao_db, comeco_ok_s,
   textura, intro`. Ajustes manuais (`override`: `loop_start_s`, `loop_bars`, `exact`, `avoid`, `intro`, `beat_mult`,
   `trim_db`). Faixa curta demais → `{"ok": False, "reason": "faixa curta demais para escolher um trecho"}` e o build
   volta ao modo antigo. Loop gravado em 48 kHz no cache (`settings.cache_dir / "music_loops"`). Opcional:
   `spectro_png` (`music_spectro`: batida azul × nota sustentada laranja por filtros de mediana em numpy, 40 Hz a 8 kHz,
   220 faixas, 16 px/s, 230 px por faixa, trecho do loop marcado); se ficar lento demais, deixe de fora e anote.
3. **`media/audio.py`**: `build_beds(beds, total, out)` = `music_beds.build`: cada cama com `src`, `start_s`, `dur_s`,
   `loop` (`(início, tamanho)` ou `None`), `intro`, `gain_db`; emenda do loop com **0,08 s** de crossfade seno/cosseno;
   **0,6 s** de sobra cruzando com a cama seguinte; fade de entrada **0,4 s** (sem intro) e de saída **1,4 s** (até 1/3
   da cama); cada loop a **−21 LUFS** com corte pela banda da voz `-min(3, max(0, (voz_db - (-18)) × 0,5))` dB (`trim_db`
   manual vence). E `music_stem(...)`: só a trilha depois do passa-altas, do ganho e do ducking, com o mesmo grafo do
   `_premix` (para a conferência).
4. **`build.py`**: faixas ativas em ordem do segmento de início (`start_segment` vazio = começo); início de cada cama =
   início da narração daquele segmento na timeline (`timeline["narration"]`), a 1ª em 0; procedural por clima com semente
   por faixa (`seed_from(doc.seed, "music", cue.id)` a partir da 2ª; a 1ª mantém a semente de hoje); biblioteca com
   `pick_track` e, com `cue.loop`, o loop curado. **Uma faixa sem `loop` = exatamente o caminho e o hash de hoje (v3):
   projeto antigo não renderiza o áudio de novo**; várias faixas ou `loop` → `build_beds` e `"v": 4`. `music_used`
   mantém `source/track/mood` (da 1ª) e ganha `beds: [{cue_id, track, mood, start_s, dur_s, loop}]`. **Guarda de apito**
   no `mix`: cada tipo de SFX usado passa por `tone_report` (cache no store, tipo `sfx_guard`); apito ou nome em
   `SFX_BANNED` → o evento sai do mix e entra o aviso "efeito sonoro '<tipo>' tem apito: ficou fora do mix (padrão do
   estúdio: sem apito)". Os 5 efeitos procedurais (`whoosh, impact, glitch, static, riser`) **têm de passar**; se um
   falhar, corrija o gerador (não afrouxe a regra) e conte no relatório final. `music_track(timeline)` devolve a trilha já
   montada (para a conferência).
5. **`document.py`**: `MusicCue.loop: bool = False` ("só a batida, em loop no compasso"). **`ops.py`**: `SetMusic.loop:
   bool | None = None`, aplicado à faixa.
6. **`workers/audiocheck/report.py`**: `run_audio_check(project_id, lang, source="preview" | "final")` lê
   `settings["last_preview" | "last_final"]["langs"][lang]` (timeline e mix), recoloca a voz (`place_clips` da narração)
   e os SFX, pega a trilha (`Builder.music_track`), gera o `music_stem` com ducking, mede (`chapter_levels`, `sfx_events`
   top 15, `tone_report` de cada tipo de SFX) e grava `QualityReport(kind="audio", lang, version, source=<mix>, status,
   items, data={"chapters": [...], "episode": {...}, "sfx_events": [...], "sfx_files": [...]})`. Sem renderizar o vídeo.
7. **`tasks.py`**: `@job("audio_check")` `{lang?, source?}`; `@job("music_curate")` `{tracks?: [caminhos]}` (vazio =
   todas da biblioteca), cache em `AppSetting["music_curation"]` pela chave `caminho|tamanho|mtime`.
8. **`routes_audio.py`**: `GET /api/library/music/curation`; `POST /api/library/music/curate`;
   `GET /api/library/music/spectro?track=&dur=100` (PNG, opcional); `GET /api/sfx/guard` (relatório dos SFX
   procedurais); `GET|POST /api/projects/{id}/audio-check` (`GET ?lang=` devolve o último relatório; `POST {lang?,
   source?}` = job).
9. **Web**: no `MusicPanel` (`AudioPanels.tsx`), "Faixas do vídeo": cada faixa com segmento de início (lista com os
   capítulos), faixa da biblioteca ou clima, volume, "Só a batida, em loop no compasso" (mostra bpm, início do loop e
   banda da voz; "Curar as faixas" quando falta), "Trocar a trilha neste segmento" (`set_music` com `start_segment`,
   nova faixa já com `loop` ligado) e desligar. `audio/AudioCheckPanel.tsx` (montado pela fundação): "Conferir o áudio
   (sem renderizar)", tabela por capítulo (voz, trilha sob a fala, trilha nas pausas, distância com selo ok/aviso pela
   faixa de 14 a 19 dB), efeitos (tempo, cena, dB × voz, tom) e a guarda de apito.
10. **Testes**: `test_audio_check.py` (seno de 3 kHz por 200 ms → apito; ruído rosa e os 5 SFX procedurais → sem apito;
    `tonal_fraction` de tom puro > 0,3; LUFS momentânea/integrada contra o `ebur128` do FFmpeg em até 0,5; máscara de
    fala; voz sintética + trilha 17 dB abaixo → ok e 8 dB abaixo → aviso; job `audio_check` sobre uma prévia offline grava
    o `QualityReport`). `test_music_beds.py` (faixa sintética de 120 bpm com um trecho de "voz" em 1 a 4 kHz → o loop
    evita o trecho; tamanho do loop múltiplo do compasso em até 2%; `build_beds` com a duração exata e sem estalo na
    emenda; 2 faixas → camas trocam no segmento; 1 faixa sem `loop` → mesmo hash de mix de antes; SFX com apito sai do mix
    com aviso). **Pronto** quando `tests/test_audio_check.py tests/test_music_beds.py tests/test_audio_mix.py
    tests/test_music_library.py tests/test_pipeline.py tests/test_shorts_from_long.py` e `npm run typecheck` passam.

### H: conferência do vídeo final antes de publicar

**Origem (só leitura):** `C:\Users\lucas\canais\motor\passos\check_triggers.py`, `check_render.py`, `frames.py`;
`estudio\skills\estudio\estudio-revisao\SKILL.md` (técnico: −14 LUFS e pico ≤ −1 dB, voz × música, gatilhos com 2 s ou
mais); `padrao.md` (regras 10, 11, 15); README do Vulto §3.8 (checklist antes de publicar).

**Arquivos (só seus):** `backend/studio/workers/finalcheck/{measure.py, triggers.py, contact.py, quick.py, report.py,
tasks.py}`, `backend/studio/api/routes_checks.py`, `backend/studio/api/routes_publish.py`,
`backend/tests/test_final_check.py`, `apps/web/src/apiChecks.ts`, `apps/web/src/pages/project/OutputTab.tsx`,
`apps/web/src/pages/project/delivery/publishing.tsx`, `apps/web/src/pages/project/delivery/PreflightPanel.tsx`,
`apps/web/src/pages/project/delivery/preflight/*`. Grava `QualityReport(kind="final")`; lê `QualityReport(kind="audio")`
(G) e `FactClaim` (F).

1. **`triggers.py`** (puro, sobre a timeline salva em `last_final.langs[lang].timeline`): para cada cena,
   `dur = durationInFrames / fps`; elementos de texto e quando aparecem: `text` da cena e manchete/valor do `card` no
   quadro 0 (se não houver `reveal`), `reveal` (stat/compare) no quadro dele, carimbo em `card.stamp_from` (ou
   `STAMP_DELAY = 6` quadros). Tempo na tela antes do corte = `dur − aparição`. **Mínimo 2,2 s** para destaques e **1,3 s**
   para carimbo/placar/anotação (`check_triggers.py`); abaixo → aviso "texto '<início do texto>' fica X s na tela antes do
   corte (mínimo 2,2 s): alongue a fala da cena, divida a cena ou leve o texto para a próxima". Leia os componentes de
   `renderer/src` e inclua outros elementos que entram em quadro conhecido pela timeline (itens de lista, contagem,
   tipografia cinética). Não repita a manchete de notícia (`news_reading_warnings` já cuida).
2. **`measure.py`**: `loudness(video)` com `audio.measure_loudness` (LUFS, LRA, pico real); alvo =
   `style.audio.loudness_lufs` (padrão −14): **±1 LU ok**, fora disso aviso; **pico real > −1,0 dBTP** → aviso.
   `voice_vs_music(video, timeline)`: respiros entre narrações (`timeline["narration"]`: fim de uma ao começo da próxima,
   ≥ 0,25 s); até 4, espalhados (posições 1/8, 3/8, 5/8, 7/8 da lista); fala = 1,0 s a partir de 40% da narração anterior;
   só música = do início do respiro + 0,03 s por `min(0,18, respiro − 0,06)` s; `mean_volume` do `volumedetect`
   (`check_render.vol`); diferença < 10 dB → aviso "música só X dB abaixo da voz no respiro de m:ss"; fala abaixo de
   −50 dB → ignora. `black_segments(video)`: `blackdetect=d=0.5:pix_th=0.10`, ignorando o primeiro e o último 0,5 s →
   aviso por trecho. Duração: estilo Short e duração > 180 s → **erro** "Short com N s passa de 3 min e não vira Short";
   Short > 60 s com mídia editorial → aviso (padrão, regra 15: Short de mais de 1 min com reivindicação fica bloqueado
   no mundo todo).
3. **`contact.py`** (`check_render` + `frames`): **12 quadros reais** do arquivo final em `t = dur × (k + 0,5) / 12`
   (`-ss t -frames:v 1`); horizontal: 3 colunas × 4 linhas, 640 px de largura; vertical: 6 colunas × 2 linhas, 480 px de
   altura; rótulo "m:ss" em (6, 4), vermelho (200, 0, 0); JPG qualidade 86 em `outputs/<projeto>/<lang>/conferencia-quadros.jpg`.
4. **`quick.py`** (sem FFmpeg, rápido): vídeo final do idioma existe (senão erro "renderize o vídeo final"); versão da
   timeline (`version`) diferente de `current_version` → aviso "o vídeo final é da versão N; o projeto está na M";
   provedor só de prévia → **erro**; mídia editorial → aviso; Short > 180 s → **erro**; checagem: `FactClaim` aberta com
   veredito `corrigir` ou `sem_fonte` → **erro** ("o que não tem fonte sai", padrão regra 2), `boato` aberta → aviso,
   desatualizadas → aviso, estilo com `script.fact_check` e nenhuma checagem → aviso; relatório de áudio do idioma: itens
   `erro` (apito) → **erro**, avisos de distância → um aviso resumido, sem relatório → item ok "rode a conferência de
   áudio na Prévia"; sem miniatura → aviso; sem legendas → aviso; divulgação de IA que vai no envio → item ok informativo.
5. **`report.py`** e **`tasks.py`**: `@job("final_check")` `{lang}` junta `quick` + medidas + gatilhos + folha e grava
   `QualityReport(kind="final", lang, version=<da timeline>, source=<vídeo relativo>, status=<pior item>, items,
   data={"lufs", "lra", "true_peak", "duration", "pauses": [...], "black": [...]}, contact_sheet)`.
6. **`routes_checks.py`**: `GET /api/projects/{id}/preflight?lang=` → `{lang, video, report | null, fresh (relatório do
   mesmo vídeo e da mesma versão), quick: [itens], status}`; `POST /api/projects/{id}/preflight {lang}` (job).
7. **`routes_publish.py`**: `YouTubePublishRequest.ignore_checks: bool = False`. Antes de submeter: status = pior de
   `quick` + relatório fresco; privacidade diferente de `private`, status `erro` e sem `ignore_checks` → `HTTPException(409,
   "A conferência antes de publicar achou problema: <até 3 títulos>. Corrija, envie como privado ou confirme 'publicar
   mesmo assim'.")`. Privado sempre passa. Não mexa no job (`publish_tasks.py` é de I).
8. **Web**: `PreflightPanel.tsx` (montado pela fundação no `OutputTab`): título ("Pronto para publicar" / "N avisos" /
   "N problemas"), "Conferir agora" (e roda sozinho uma vez quando há vídeo final sem relatório fresco), itens por nível,
   medidas (LUFS, pico real, duração), folha de contato clicável, aviso de relatório velho. Em `publishing.tsx`: no envio
   público/agendado com erro (ou resposta 409), diálogo com os erros e "Publicar mesmo assim" (`ignore_checks: true`) ou
   "Enviar como privado". No `OutputTab`, selo do estado perto do passo de publicar.
9. **Testes `test_final_check.py`**: gatilhos numa timeline sintética (reveal perto do fim, carimbo com 1,0 s, texto em
   cena de 1,5 s); `quick` com projeto Short, `last_final` falso e `FactClaim` aberta → erro; medidas num MP4 sintético
   (`lavfi`: `testsrc` 10 s com 1 s de preto no meio + seno a −20 dBFS com um respiro) → LUFS, preto achado, folha com 12
   quadros no tamanho certo; trava: 409 em público com erro, passa em privado e com `ignore_checks` (runner com
   `monkeypatch`). **Pronto** quando `tests/test_final_check.py tests/test_youtube.py tests/test_real_media_publish.py
   tests/test_api.py` e `npm run typecheck` passam.

### I: visual (provas na tela, vídeo de referência, miniaturas A/B)

**Origem (só leitura):** `C:\Users\lucas\canais\motor\passos\shot_helper.py` (`FIND_JS`, `RECTS_JS`, `CLEAN_JS`,
`shoot`), `shot_views.py` (`ink_mask`, `view_for`, página escura), `trailer_shots.py`, `thumbs.py`;
`estudio\skills\estudio\estudio-publicacao\references\titulos-miniaturas.md` (A/B e 3 palavras); `padrao.md` (regras 1,
10 e 15).

**Arquivos (só seus):** `backend/studio/media/page_capture.py`, `backend/studio/media/reference_video.py`,
`backend/studio/media/thumb_variants.py` (novos), `backend/studio/media/thumbnail.py`,
`backend/studio/workers/visual/{proofs.py, references.py, thumbs.py, tasks.py}`, `backend/studio/api/routes_visual.py`,
`backend/studio/workers/publish_tasks.py`, `backend/studio/workers/publish/package.py`, `backend/tests/test_visual.py`,
`apps/web/src/apiVisual.ts`, `apps/web/src/pages/References.tsx`, `apps/web/src/pages/references/*`,
`apps/web/src/pages/project/RealMediaPanel.tsx`, `apps/web/src/pages/project/proof/*`,
`apps/web/src/pages/project/delivery/ThumbVariants.tsx`, `apps/web/src/pages/project/delivery/thumbs/*`. Tabelas:
`PageCapture`, `ReferenceVideo`; escreve `Project.settings["thumbs"]` e `PendingTask`; lê `VideoMetric`.

1. **`media/page_capture.py`**: `browser_status()` (`importlib.util.find_spec("playwright")`; mensagem do ADR, decisão
   4); `capture(url, phrases, focus=0, *, open_page=None)`: `safe_http.check_url` primeiro; Playwright síncrono com
   `channel="msedge"`, depois `"chrome"`, depois o padrão; janela **1100 × 680**, `device_scale_factor=2`; espera a carga
   e **2,5 s**; roda `CLEAN_JS`, `FIND_JS` (trecho de foco a **42%** da altura, `__SHOT_POS`), espera 1,5 s, roda de novo,
   espera 0,8 s, lê `RECTS_JS`; repete até 5 vezes (0,9 s) até duas leituras iguais; captura a janela; guarda
   `document.body.innerText`. JS copiado do canais (comentários em PT). `open_page` injetável para os testes. Funções
   puras: `ink_mask(img)` (mediana do cinza < 110 → página escura, texto = > 150; senão texto = < 150), `is_dark(img)`
   (64 × 40 em cinza, média/255 < 0,4), `view_for(mask, rects, W, H)` = `shot_views.view_for` (faixa de 1,6 linha,
   folga de 1,4 linha, tolerância de 3% da largura entre palavras, margem de 2%, `z = max(1, min(1,6, min(1/(vx1-vx0),
   1/(vy1-vy0)) × 0,94))`, até 8 voltas até a coluna estabilizar), `mark(img, rects, dark)` (marca-texto #FFE14D:
   multiply nas páginas claras, camada de 45% nas escuras; folga de 15% da altura da linha), `crop_to_view(img, view,
   aspect)` (janela de W/z × H/z centrada no trecho, presa à imagem e alargada até a proporção do vídeo, 16:9 ou 9:16,
   **sem cortar a janela do trecho**).
2. **`workers/visual/proofs.py`**: `capture_proof(project_id, scene_id, url, phrases (1 a 6), focus)`: captura, grava PNG e
   texto em `uploads/<projeto>/captures/<id>.png|.txt`, calcula as janelas (`status="parcial"` se um trecho não foi
   achado ou ficou fora da tela), monta a prova (marca + recorte), guarda com `real_media.store_image` e põe na cena como
   `MediaRef(kind="official", source="link", page_url=url, title=<título da página>, outlet=<domínio>,
   credit=<domínio>, editorial=False, width, height)` pelo mesmo caminho que a rota de mídia usa (leia
   `api/routes_media.py`); grava a `PageCapture`. `rebake(capture_id, i, view, lock)` para ajuste à mão (`lock`
   preserva a janela, como no canais).
3. **`media/reference_video.py`**: `probe` (ffprobe: largura, altura, fps, codec, duração); `cuts(path, thr=0.3)`
   (`scale=320:-2,select='gt(scene,0.3)',showinfo`, lê `pts_time`); planos = `[0] + cortes > 0,3 + [dur]`, **plano < 0,4 s
   sai** (flash ou corte duplo); `contact_sheets` (**5 × 4** quadros de **384 × 216**, 216 × 384 para vídeo vertical,
   quadro no meio do plano, rótulo "#k a s (dur)" amarelo (255, 230, 80) em caixa preta, 20 planos por folha, JPG 82);
   `stats` (planos, duração mediana e média, cortes por minuto, paleta de 5 cores por k-means em numpy com início
   determinístico sobre os quadros do meio reduzidos a 64 px); `download(url, out)` com yt-dlp opcional
   (`-f "bv*[height<=1080]+ba/b[height<=1080]" --merge-output-format mp4`, 900 s) e a mensagem do ADR quando falta.
   Arquivos em `library/references/`. A tela avisa: referência é para estudar ritmo e enquadramento, não para reusar.
4. **`media/thumb_variants.py`** e **`workers/visual/thumbs.py`**: 3 variações (`style.thumbnail.variations`, até 3):
   **1ª = a do projeto** (`doc.thumbnail`: cena, texto, modelo; rótulo "Recomendada"), **2ª = outra cena** (a primeira
   com imagem, não editorial, de tipo `stat`/`compare`/`title` ou do gancho, diferente da 1ª; mesmo texto), **3ª = mesma
   cena com outro modelo** (`texto-grande-esquerda` → `texto-centro` → `texto-baixo`). Editáveis em
   `settings["thumbs"][lang]["spec"]`. Tamanho: longo **3840 × 2160**, Short 1080 × 1920; JPG 92 e, se passar de **2 MB**,
   qualidade 88, 84, 80, 76; ainda acima ou tamanho errado → erro "passa de 2 MB (limite pelo celular)". Texto do longo
   com mais de 3 palavras → aviso "miniatura do longo: no máximo 3 palavras". Prévia 1280 × 720 (LANCZOS, 90). Folhas:
   grande (prévias empilhadas) e pequena (larguras **168, 246 e 360** px, alturas 94, 138, 202, 40 px entre elas, fundo
   (15, 15, 15), 230 px por linha, selo da duração com `fs = max(9, round(altura × 0,12))`, caixa preta arredondada
   (raio `fs × 0,3`), texto branco, rótulo "n. rótulo" em cinza (200, 200, 200)); duração "m:ss" do vídeo final.
   `ab_rule(channel_id)`: longos do canal com métrica de 7 dias (`VideoMetric` com `age_hours` ≥ 168, a linha mais perto
   de 168 h por vídeo); mediana das impressões não vazias **≥ 2.000 → teste A/B** ("use o 'Testar e comparar' do YouTube
   Studio com as 3: a API não faz o teste"); senão "suba a 1ª; em 48 h, CTR abaixo de 4% → troque pela 2ª".
   `media/thumbnail.py` só muda se precisar (ex.: qualidade do JPG como parâmetro), sem quebrar o build.
5. **`publish_tasks.py`**: a miniatura enviada é `variants[chosen - 1].path` quando existe (contrato do ADR), senão a do
   build. Depois do envio, sem teste A/B e com 2+ variações: `PendingTask(kind="auto", auto_key="miniatura:<video_id>",
   text='Em 48 h, ver o CTR de "<título>": abaixo de 4%, trocar a miniatura pela 2ª opção', where_hint="/projetos/<id>",
   channel_id)`; `Publication.details["thumbs"] = {"chosen", "variants", "ab_eligible"}`. O resto (afiliados, créditos,
   legendas) fica como está. **`package.py`**: copia as variações para o pacote (`miniatura-1.jpg`…) e põe a regra do A/B
   no `CHECKLIST.md`.
6. **`tasks.py`**: `@job("page_capture")` `{scene_id, url, phrases, focus}`; `@job("reference_shots")`
   `{reference_id}`; `@job("thumbs_variants")` `{lang}`.
7. **`routes_visual.py`**: `GET /api/visual/capabilities` (`{browser: {ok, message}, ytdlp: {ok, message}}`);
   `POST /api/projects/{id}/scenes/{scene_id}/proof {url, phrases, focus}` (job; 400 com a mensagem quando falta o
   navegador); `GET /api/projects/{id}/captures`; `GET /api/captures/{id}`; `PUT /api/captures/{id}/phrases/{i}/view
   {view, lock}`; `GET|POST /api/references` (POST = envio do arquivo, multipart), `POST /api/references/link {url,
   title?}`, `GET|DELETE /api/references/{id}`, `POST /api/references/{id}/analyze` (job); `GET
   /api/projects/{id}/thumbs?lang=`, `PUT /api/projects/{id}/thumbs/spec {lang, variants}`, `POST
   /api/projects/{id}/thumbs {lang}` (job), `PUT /api/projects/{id}/thumbs/chosen {lang, n}`.
8. **Web**: página **Referências** (lista; envio do arquivo; link pelo yt-dlp, desligado com a mensagem quando falta;
   "Analisar"; folhas; tabela de planos; ritmo "N cortes por minuto, plano mediano X s"; paleta). No `RealMediaPanel`,
   opção **"Página com marca-texto"**: link, até 6 trechos (um por linha), trecho de foco, estado da captura, trechos
   achados/não achados, prévia e "ajustar enquadramento" (travar). `ThumbVariants.tsx` (montado pela fundação no
   `OutputTab`): as 3 com prévia, tamanho, MB e erros, "Usar esta", editar (cena, texto, modelo), "Gerar as 3", folha
   pequena e o texto da regra do A/B.
9. **Testes `test_visual.py`**: `view_for` numa imagem sintética (linhas pretas) → janela com a linha inteira e `z` entre
   1 e 1,6; `crop_to_view` contém a janela e tem a proporção do vídeo; `mark` escurece o amarelo no branco e não no
   escuro; `is_dark`; captura com `open_page` falso → `PageCapture`, mídia na cena, `parcial` quando um trecho falta;
   `browser_status` sem Playwright → mensagem; planos de um vídeo `lavfi` com 3 cores de 1 s e um flash de 0,2 s → 3
   planos; folha no tamanho certo; paleta perto das 3 cores; yt-dlp ausente → mensagem; 3 miniaturas 3840 × 2160 até 2 MB;
   aviso de 4 palavras; folha pequena com 934 × 730 px (3 linhas); regra do A/B com e sem 2.000 impressões; envio usa a
   escolhida e cria a pendência (MockTransport, como `test_youtube.py`); pacote com as miniaturas. **Pronto** quando
   `tests/test_visual.py tests/test_business.py tests/test_youtube.py tests/test_real_media_publish.py
   tests/test_real_media_routes.py` e `npm run typecheck` passam.
