// Cliente da API do backend (FastAPI).

export type Segment = {
  id: string;
  speaker: string;
  role: string;
  chapter: string | null;
  text: Record<string, string>;
  locked: boolean;
};

export type SceneLook = { brightness: number; contrast: number; saturation: number; warmth: number; blur: number; grade: string | null };

/** Texto por idioma ({"pt-BR": "…"}). */
export type LocalizedText = Record<string, string>;

// Os campos marcados com "?" faltam quando o servidor ainda roda a versão anterior (antes de reiniciar o app).
export type Visual = {
  type: string;
  prompt: string;
  query: string | null;
  /** Imagem fixada (upload ou escolhida): vale para qualquer tipo. */
  asset: string | null;
  seed: number;
  variation: number;
  /** Linguagem visual (id do catálogo). Sem ela, a cena é antiga e o prompt é composto como antes. */
  language?: string | null;
  shot?: string | null;
  composition?: string | null;
  entities?: string[];
  /** Id da cena dona da imagem que esta mostra (nenhuma imagem nova). */
  reuse_of?: string | null;
  /** Mídia real anexada: vale mais que a imagem de IA, o `asset` e o `reuse_of` (que ficam de reserva). */
  media?: MediaRef | null;
  /** Por que a imagem é de IA (hipótese, futuro, conceito), dito pelo storyboard. Ausente/null: sem motivo. */
  ai_reason?: string | null;
};

/* ------------------------------------------------------------------ */
/* Mídia real (foto livre, cartão de notícia, material oficial)        */
/* ------------------------------------------------------------------ */

/** photo: foto de licença livre; news: cartão de notícia; official: arte, trailer ou foto de imprensa oficial. */
export type RealKind = 'photo' | 'news' | 'official';
/** O que a cena mostra de fato (ADR 0004): as imagens de `RealKind` ou um trecho de vídeo, uma captura, um meme. */
export type MediaKind = RealKind | 'clip' | 'capture' | 'meme';
export type MediaSource = 'commons' | 'openverse' | 'pexels' | 'link' | 'upload' | 'youtube' | 'library' | 'capture';
/** Nível (origem) do material na tela, do mais real ao gerado (`Prov` em core/project/document.py). */
export type Prov = 'own' | 'official' | 'capture' | 'free' | 'stock' | 'graphic' | 'meme' | 'ai';
/** cover: preenche a tela (recorta); contain: imagem inteira sobre ela mesma desfocada; null: pela proporção. */
export type MediaFit = 'cover' | 'contain';

/** Mídia real anexada à cena (Visual.media em core/project/document.py). */
export type MediaRef = {
  kind: MediaKind;
  source: MediaSource;
  /** Original normalizado na pasta de dados (use com `fileUrl`); null só na notícia sem imagem. */
  asset: string | null;
  url: string;
  page_url: string;
  title: string;
  author: string;
  /** Nome curto da licença; vazio: direitos reservados ou desconhecida. */
  license: string;
  license_url: string;
  /** Texto do crédito sem o prefixo ("Foto:" / "Imagem:" vêm do idioma do vídeo). */
  credit: string;
  /** Material de terceiros com direitos: aviso de Content ID. */
  editorial: boolean;
  width: number | null;
  height: number | null;
  /** Id de foco (onde está o sujeito na foto). */
  focus: string | null;
  fit: MediaFit | null;
  outlet: string;
  /** "YYYY-MM-DD", "YYYY-MM", "YYYY" ou vazio. */
  published: string;
  headline: LocalizedText;
  candidate_id: string;
  /* Campos do ADR 0004 (servidor antigo: ausentes). */
  /** Proxy mp4 do trecho (relativo à pasta de dados); `asset` é o pôster. */
  video?: string | null;
  start_s?: number;
  dur_s?: number | null;
  hold?: boolean;
  mute?: boolean;
  /** Captura: trechos destacados `[x, y, w, h]` (0 a 1). */
  rects?: number[][];
  tier?: Prov | null;
  /** Item do acervo (`pool.json`) de onde a mídia veio. */
  pool_id?: string;
};

/** Sugestão de mídia real do storyboard (Scene.real). */
export type RealHint = {
  kind: RealKind;
  /** Busca em inglês para o Commons/Openverse. */
  query: string;
  /** O que mostrar e onde achar (português). */
  note: string;
  /** Posição (base 1) em `doc.sources` da fonte com link (notícia). */
  source_ref: number | null;
  /** Mídia oficial/notícia achada pelo job "media_find" (nada é anexado sozinho). */
  found?: FoundMedia[];
  /** A busca já rodou para esta sugestão (mesmo sem achar nada). */
  searched?: boolean;
  /** Quando a busca rodou por último (ISO, UTC): a busca de novo começa pelas mais antigas. */
  searched_at?: string;
};

/** Imagem achada na web pelo job "media_find" (FoundMedia em core/project/document.py). */
export type FoundMedia = {
  /** URL direta da imagem (miniatura e o que é anexado em oficial/foto). */
  image: string;
  /** Página onde foi achada (notícia ou página de imprensa oficial). */
  page_url: string;
  /** Descrição curta da imagem ou manchete da notícia. */
  title: string;
  /** Site ou veículo ("Rockstar Games", "PC Gamer"). */
  outlet: string;
  kind: RealKind;
  width: number;
  height: number;
  /** Por que serve para a cena (uma frase). */
  why: string;
};

/** Situação de cada fonte na busca: ok, vazio (nada achado), erro (não respondeu), offline, sem_chave (Pexels). */
/** `sem_contato`: o Commons pede um contato (Configurações) e a busca foi só no Openverse. */
export type MediaSearchStatus = 'ok' | 'vazio' | 'erro' | 'offline' | 'sem_chave' | 'sem_contato';

/** Foto de licença livre achada na busca (nada é aplicado até o usuário escolher). */
export type MediaCandidate = {
  /** "commons:File:X.jpg", "openverse:<uuid>" ou "pexels:<id>". */
  id: string;
  source: 'commons' | 'openverse' | 'pexels';
  title: string;
  author: string;
  license: string;
  license_url: string;
  page_url: string;
  /** Miniatura remota (só para mostrar na tela). */
  thumb: string;
  width: number;
  height: number;
  /** Crédito pronto (sem o prefixo). */
  credit: string;
  attribution_required: boolean;
  /** Aviso curto ("pode ter direito de imagem de pessoa ou marca"); vazio sem aviso. */
  warning: string;
};

/** GET /api/projects/:id/scenes/:sceneId/media/candidates */
export type MediaCandidates = {
  query: string;
  candidates: MediaCandidate[];
  sources: Partial<Record<'commons' | 'openverse' | 'pexels', MediaSearchStatus>>;
  cached: boolean;
};

/** POST .../media, POST .../media/upload, PATCH .../media */
export type MediaAttachResult = { version: number; media: MediaRef; warnings: string[]; job: Job | null; notice?: string | null };

/** PATCH .../media (só os campos enviados mudam; `headline` vale para `lang`, padrão o idioma principal). */
export type MediaPatch = {
  kind?: RealKind;
  credit?: string;
  editorial?: boolean;
  focus?: string | null;
  /** 'auto' volta para a escolha pela proporção. */
  fit?: MediaFit | 'auto';
  outlet?: string;
  published?: string;
  headline?: string;
  lang?: string;
  rebuild?: boolean;
};

/** GET /api/projects/:id/media/credits?lang= : o que vai no fim da descrição ao publicar. */
export type MediaCredits = {
  lang: string;
  /** Título da lista no idioma ("Créditos das imagens"). */
  heading: string;
  lines: string[];
  /** Bloco pronto (título + linhas); vazio sem mídia real. */
  text: string;
  /** Algum material de terceiros com direitos (aviso de Content ID). */
  editorial: boolean;
};

/** Linha da comparação, ponto do gráfico ou região do mapa (`region`: código como "BRA" ou "US-TX"). */
export type SceneCardItem = { label: LocalizedText; value: LocalizedText; region?: string | null };

export type ChartType = 'bar' | 'line' | 'pie' | 'donut';

/** Cartão (número, comparação, gráfico, mapa) e sobreposições (faixa, carimbo, fonte) da cena. */
export type SceneCard = {
  value: LocalizedText;
  label: LocalizedText;
  items: SceneCardItem[];
  highlight: number | null;
  /** Tipo do gráfico (cena `chart`); null nas outras. */
  chart?: ChartType | null;
  headline: LocalizedText;
  stamp: LocalizedText;
  source: string | null;
};

export type Scene = {
  id: string;
  segment_id: string;
  part: number;
  parts: number;
  kind: string;
  visual: Visual;
  overlay_text: Record<string, string>;
  number: number | null;
  motion: string | null;
  transition_in: string | null;
  look: SceneLook;
  sfx: string | null;
  locked: boolean;
  /** Palavras do trecho (idioma principal) que a cena cobre. Sem ele, a divisão é a antiga. */
  span?: number | null;
  card?: SceneCard | null;
  /** Para onde a câmera olha (id de foco). */
  focus?: string | null;
  /** Sugestão de mídia real do storyboard (null: nenhuma). */
  real?: RealHint | null;
  /** Vídeo IA da cena-chave (vale com kind "video"): movimento em inglês e duração pedida. */
  clip?: ClipSpec | null;
  /** Sugestão do storyboard: esta cena ganha com vídeo IA (o texto é o movimento). Nada é aplicado sozinho. */
  video_hint?: string | null;
  /** Avatar do apresentador nesta cena (null/ausente: sem avatar). */
  presenter?: ScenePresenter | null;
  /** Subplanos da cena (ADR 0004 §3.6); vazio: um plano só. */
  shots?: SubShot[];
  /** Eventos presos a palavras (punch, texto, SFX, meme…). */
  fx?: FxEvent[];
  /** Papel da batida, ênfase e piada (do storyboard ou do planejador). */
  mark?: BeatMark | null;
};

/* ------------------------------------------------------------------ */
/* Camada de edição (ADR 0004 §3.0, core/project/document.py)          */
/* ------------------------------------------------------------------ */

export type FxType =
  | 'punch'
  | 'shake'
  | 'zoom_rect'
  | 'freeze'
  | 'flash'
  | 'text_pop'
  | 'meme'
  | 'sfx'
  | 'highlight'
  | 'arrow'
  | 'circle'
  | 'counter'
  | 'music_drop'
  | 'speed'
  | 'whip'
  | 'pip';
export type EditOrigin = 'planner' | 'llm' | 'user';
export type FxParam = string | number | boolean | number[];

/** Evento dentro da cena. `at`: "palavra", "palavra#2" (2ª vez na cena) ou "@1200" (ms desde o início da cena). */
export type FxEvent = {
  type: FxType;
  at: string;
  dur_ms: number | null;
  params: Record<string, FxParam>;
  origin: EditOrigin;
  locked: boolean;
};

export type ShotSource = 'self' | 'pool' | 'media' | 'reuse' | 'ai';
export type SubShot = {
  /** Gatilho do corte; null: o início da cena. */
  at: string | null;
  source: ShotSource;
  pool_id: string | null;
  media: MediaRef | null;
  reuse_of: string | null;
  framing: { scale: number; x: number; y: number } | null;
  motion: string | null;
  easing: string | null;
  strength: number | null;
  clip_in: number | null;
  speed: number;
  hold: boolean;
  origin: 'planner' | 'user';
  locked: boolean;
};

export type BeatRole = 'hook' | 'reveal' | 'joke' | 'aside' | 'list' | 'number' | 'tension' | 'breath' | 'cta';
export type BeatMark = {
  role: BeatRole;
  emphasis: string[];
  energy: number;
  serious: boolean;
  gag: { at: string | null; payoff: 'meme' | 'freeze' | 'zoom' | 'sfx' | 'text'; tag: string; line: string } | null;
};

/* ------------------------------------------------------------------ */
/* Fontes e gramática de edição do estilo (core/styles/schema.py)      */
/* ------------------------------------------------------------------ */

/** De onde vem o que aparece na tela: mistura por nível e teto de IA em TEMPO de tela. */
export type SourcePolicy = {
  mix: Partial<Record<Prov, number>>;
  ai_max_share: number;
  ai_photo: boolean;
  hook: Prov[];
  official_max_clip_s: number;
  official_max_share: number;
  require_ai_reason: boolean;
};

export type EditGrammar = {
  editor: 'off' | 'auto';
  intensity: number;
  shot_median_s: number;
  shot_cv: number;
  shot_max_s: number;
  micro_per_min: number;
  burst_prob: number;
  punch_per_min: number;
  punch_scale: { min: number; max: number };
  shake_per_min: number;
  text_pop_per_min: number;
  meme_per_min: number;
  annot_per_min: number;
  sfx_per_min: number;
  sfx_on_cut_share: number;
  sfx_palette: Record<string, number>;
  sfx_tonal_ok: boolean;
  switch_per_min: number;
  max_still_s: number;
  slow_zoom_share: number;
  static_share_min: number;
  humor: 'none' | 'light' | 'meme';
  caption_emphasis: 'none' | 'color' | 'pop' | 'emoji';
  effects_policy: 'global' | 'per_tier';
  music_drop_before_reveal: boolean;
  hook: { window_s: number; shot_median_s: number | null; punch_per_min: number | null; text_pop_per_min: number | null; sfx_per_min: number | null } | null;
  profile_refs: string[];
  tolerance: number;
};

export type ClipSpec = { prompt: string; seconds: number };

/* --- Avatar do apresentador (espelho de core/project/document.py) --------------------------------------------- */
export type PresenterMode = 'reacao' | 'explica';
export type PresenterSide = 'auto' | 'left' | 'right';
/** Avatar numa cena. `at`: palavra, "palavra#2" ou "@1200" (ms na cena). `source: 'manual'` a distribuição não troca. */
export type ScenePresenter = {
  pose: string | null;
  at: string | null;
  mode: PresenterMode;
  side: PresenterSide;
  source: 'auto' | 'manual';
  off: boolean;
};
export type PresenterPose = {
  id: string;
  /** PNG RGBA original (caminho relativo na pasta de dados: library/presenter/<canal>/<pose>.png). */
  file: string;
  /** Versão de 900 px de altura (vídeo). */
  render_file: string | null;
  emotions: string[];
  aliases: string[];
  thumb_ok: boolean;
  facing: 'center' | 'left' | 'right';
};
export type PresenterVideo = {
  every_s: [number, number];
  min_gap_scenes: number;
  height_reacao: number;
  height_explica: number;
  outline_px: number;
  hide_on_kinds: string[];
  hide_on_vertical: boolean;
  skip_first_scene: boolean;
};
/** Avatar do canal (style_bible.presenter). Desligado por padrão. */
export type Presenter = {
  enabled: boolean;
  character_id: string | null;
  poses: PresenterPose[];
  default_pose: string;
  video: PresenterVideo;
  use_in_thumb: boolean;
  use_in_video: boolean;
  use_in_shorts: boolean;
  thumb_height: number;
  thumb_crop: 'busto' | 'meio';
  thumb_outline_px: number;
};
/** Avatar no projeto: null segue o canal. */
export type PresenterOverride = { enabled: boolean | null; thumb: boolean | null; video: boolean | null; poses_off: string[] };
/** Preferências de thumb do canal (style_bible.thumbs). `allow_editorial` null segue o estilo. */
export type ChannelThumbs = { allow_editorial: boolean | null; ai_official_characters: false };

/* --- Pacote: título e thumb antes do roteiro (espelho de core/project/document.py) ------------------------------ */
export type ThumbTemplate = 'heroi-palavra' | 'avatar-reage' | 'lugar-mapa' | 'numero-lista' | 'versus' | 'livre';
export const THUMB_TEMPLATES: ThumbTemplate[] = ['heroi-palavra', 'avatar-reage', 'lugar-mapa', 'numero-lista', 'versus', 'livre'];
export type BgLayer = {
  kind: 'frame' | 'media' | 'upload' | 'gradient' | 'ai';
  ref: string | null;
  /** Segundos no trailer (frames de vídeo). */
  t: number | null;
  blur: number;
  darken: number;
  zoom: number;
  /** Onde fica o assunto no quadro (fração da largura). */
  focus_x: number;
};
export type CutoutLayer = { asset: string; x: number; y: number; h: number; flip: boolean };
export type PresenterThumbLayer = { pose: string; side: 'left' | 'right'; crop: 'busto' | 'meio'; h: number; outline_px: number; flip: boolean };
export type TextLayer = { words: string; fill: 'yellow' | 'vice' | 'white'; pos: 'top' | 'left' | 'center'; size: number };
export type CardLayer = { asset: string | null; label: string };
export type ArrowLayer = { from_x: number; from_y: number; to_x: number; to_y: number; color: string };
export type ThumbLayers = {
  template: ThumbTemplate;
  background: BgLayer;
  big_vi: boolean;
  characters: CutoutLayer[];
  presenter: PresenterThumbLayer | null;
  text: TextLayer | null;
  pill: string | null;
  cards: CardLayer[];
  logo: 'corner' | 'none';
  arrow: ArrowLayer | null;
  strike: boolean;
};
/** Saída da checagem de legibilidade (`thumb_check`): métricas e avisos (nunca bloqueia). */
export type ThumbScore = {
  words?: number;
  text_h?: number;
  legib_168?: number;
  face_h?: number;
  luminance?: number;
  warnings?: string[];
  [key: string]: unknown;
};
export type PackageCandidate = {
  id: string;
  title: string;
  thumb_text: string;
  /** O que o espectador ganha: o contrato do gancho. */
  promise: string;
  concept: string;
  layers: ThumbLayers;
  score: ThumbScore;
  /** Prévia renderizada (caminho relativo na pasta de dados). */
  image: string | null;
  approved: boolean;
  source: 'auto' | 'manual';
};
export type PackageDoc = {
  candidates: PackageCandidate[];
  chosen_id: string | null;
  /** Aprovado: roteiro e metadata não trocam o título nem o texto da thumb. */
  locked: boolean;
  references: string[];
  generated_at: string | null;
};

/** Estado do vídeo IA do projeto (GET /api/projects/{id}/video). */
export type VideoClipItem = {
  scene_id: string;
  label: string;
  seconds: number;
  prompt: string;
  hint: string;
  status: 'ready' | 'missing' | 'blocked';
  detail: string;
  clip_seconds?: number;
  cost_usd?: number;
  provider?: string | null;
};
export type VideoStatus = {
  max_scenes: number;
  provider: string | null;
  provider_name: string | null;
  available: boolean;
  reason: string;
  chain: string[];
  scenes: VideoClipItem[];
  missing: number;
  estimated_cost_usd: number;
  max_cost_usd: number;
  suggestions: string[];
  job: Job | null;
};

export type ArtEntity = { id: string; name: string; look: string; real?: boolean };

/** Direção de arte do vídeo, decidida pelo assunto (o estilo é a referência). Textos livres em inglês. */
export type ArtDirection = {
  genre: string;
  approach: 'literal' | 'misto' | 'alegorico';
  world: string;
  treatment: string;
  palette: string[];
  languages: string[];
  entities: ArtEntity[];
  avoid: string[];
  grade: string | null;
  style_fit: 'ok' | 'fraco' | 'errado';
  style_note: string;
  suggested_style: string | null;
  hook: string;
  source: 'ai' | 'user';
};

export type StoryboardIssue = {
  code: string;
  severity: 'grave' | 'aviso' | 'corrigido';
  /** "{segmento}:{k}" (para o pedido de correção). */
  scene_key: string | null;
  /** Cena do documento a que o aviso se refere (null: vale para o vídeo inteiro). */
  scene_id: string | null;
  message: string;
};

/** Revisão da curadoria gravada pelo job das cenas (settings.storyboard_review). */
export type StoryboardReview = {
  /**
   * `real_hints`: cenas com sugestão de mídia real (falta no servidor antigo). `ai_pct`/`real_pct`: parte do tempo de
   * tela com imagem de IA e com material real (só com acervo de material real; ADR 0004).
   */
  counts: {
    scenes: number;
    new_images: number;
    reused: number;
    cards: number;
    budget: number;
    est_seconds: number;
    real_hints?: number;
    ai_pct?: number;
    real_pct?: number;
  };
  issues: StoryboardIssue[];
  retried: boolean;
  style_fit: 'ok' | 'fraco' | 'errado';
  style_note: string;
  suggested_style: string | null;
  provider?: string;
  version?: number;
};

/** Paradas do piloto automático (projeto sem a chave: script e prévia valem true; storyboard e package valem false). */
export type ProjectGates = { package?: boolean; script?: boolean; storyboard?: boolean; preview?: boolean; publish?: boolean; [k: string]: boolean | undefined };

export type Metadata = { title: string; description: string; tags: string[]; hashtags: string[] };

export type ProjectDoc = {
  id: string;
  title: string;
  topic: string;
  brief: string;
  channel_id: string | null;
  style_id: string;
  style_overrides: Record<string, unknown>;
  primary_language: string;
  languages: string[];
  segments: Segment[];
  scenes: Scene[];
  music: { id: string; start_segment: string | null; mood: string | null; volume_db: number | null; enabled: boolean; track?: string | null }[];
  thumbnail: { text: Record<string, string>; scene_id: string | null; prompt: string | null; variation: number };
  metadata: Record<string, Metadata>;
  sources: { title: string; url: string; note: string }[];
  script_approved: boolean;
  preview_approved: boolean;
  /** Direção de arte (vem com as cenas). Projeto antigo: ausente ou null. */
  art?: ArtDirection | null;
  /** Cenas aprovadas na parada "conferir as cenas". */
  storyboard_approved?: boolean;
  /** Título e thumb escolhidos antes do roteiro (servidor antigo: ausente). */
  package?: PackageDoc;
  /** Avatar neste projeto (servidor antigo: ausente). */
  presenter?: PresenterOverride;
};

export type ProjectRow = {
  id: string;
  title: string;
  channel_id: string | null;
  style_id: string;
  status: string;
  current_version: number;
  settings: {
    gates?: ProjectGates;
    last_preview?: BuildResult;
    last_final?: BuildResult;
    storyboard_review?: StoryboardReview;
    [k: string]: unknown;
  };
  created_at?: string;
  updated_at: string | null;
  /** Projeto longo de onde este Short foi cortado (Shorts do longo). */
  parent_id?: string | null;
};

export type LangOutput = {
  timeline: string;
  mix: string;
  srt: string;
  duration: number;
  scenes: number;
  chapters: { title: string; start: number }[];
  video?: string;
  thumbnail?: string;
  /** Modo B: faixa de áudio deste idioma sobre o vídeo do idioma principal (sem vídeo próprio). */
  dub_of?: string;
  /** Modo B: maior aceleração aplicada à narração deste idioma (1 = nenhuma). */
  max_tempo?: number;
  /** Modo B: maior desaceleração (abaixo de 1) aplicada à narração curta deste idioma (1 = nenhuma). */
  min_tempo?: number;
};

/** Como os idiomas saem: um vídeo por idioma (Modo A) ou faixas de áudio no mesmo vídeo (Modo B, só YouTube). */
export type LanguageDelivery = 'separate_videos' | 'multi_audio_track';

/** Resumo do ajuste de tempo das faixas (Modo B). */
export type DubSummary = {
  primary: string;
  max_speedup: number;
  min_slowdown?: number;
  added_pause_s: number;
  langs: Record<
    string,
    { max_tempo: number; min_tempo?: number; stretched: number; longest_in: number; segments: number }
  >;
};

export type DubPackage = {
  folder: string;
  checklist: string;
  langs: Record<string, { folder: string; files: Record<string, string>; duration?: number | null }>;
};

export type BuildResult = {
  delivery?: LanguageDelivery;
  dub?: DubSummary;
  dub_package?: DubPackage;
  mode: 'preview' | 'final';
  langs: Record<string, LangOutput>;
  generated: Record<string, number>;
  reused: Record<string, number>;
  cost_usd: number;
  preview_only_providers: string[];
  warnings?: string[];
  music?: { source: string; track: string | null; mood: string | null } | null;
  artifacts?: string[];
};

export type LibraryTrack = { path: string; name: string; mood: string; size: number };
export type LibraryMusic = { dir: string; tracks: LibraryTrack[]; counts: Record<string, number>; moods: string[] };
/** LUTs de cor (.cube): os prontos (gerados no app) e os da biblioteca do usuário (library/luts). */
export type LutUser = { name: string; bytes: number; title?: string; size?: number; dim?: number; error?: string };
export type LutListing = { dir: string; builtin: { id: string; label: string }[]; user: LutUser[] };

/** Rótulo de cada clima da trilha (as chaves de `MOODS` em backend/studio/media/audio.py). */
export const MOOD_LABELS: Record<string, string> = {
  neutral: 'neutro (ambiente leve)',
  upbeat: 'animado',
  inspiring: 'inspirador',
  lofi: 'lo-fi',
  corporate: 'corporativo leve',
  playful: 'curioso / divertido',
  calm: 'calmo',
  epic: 'épico',
  pulse: 'pulso',
  mystery: 'mistério',
  tension: 'tensão',
  horror: 'terror',
  drone: 'sombrio (drone)',
};

export type Job = {
  id: string;
  kind: string;
  project_id: string | null;
  status: 'queued' | 'running' | 'done' | 'failed' | 'canceled';
  progress: number;
  message: string;
  payload: Record<string, unknown>;
  result: Record<string, unknown>;
  error: string | null;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
};

/** `branch`: "main" (linha principal, versão A) ou o nome de um ramo A/B ("b", "c"...). */
export type Version = { number: number; parent: number | null; message: string; author: string; branch?: string; created_at: string | null };

/** Ramo A/B de versões (a principal vem no mesmo formato, com `is_main`). */
export type AbBranch = {
  name: string;
  letter: string;
  label: string;
  note: string;
  base: number | null;
  head: number;
  status: 'principal' | 'aberto' | 'mesclado' | 'promovido' | 'descartado';
  is_main: boolean;
  last_preview: (BuildResult & { version?: number }) | null;
  last_final: (BuildResult & { version?: number }) | null;
  experiment_id: string | null;
  created_at: string | null;
  closed_at: string | null;
};

export type AbVideo = {
  publication_id: string;
  youtube_id: string;
  url: string | null;
  language: string;
  privacy?: string | null;
  public?: boolean;
  views?: number | null;
  age_hours?: number | null;
};

export type AbReading = {
  branch: string;
  letter: string;
  note: string;
  a: { videos: AbVideo[]; views: number | null };
  b: { videos: AbVideo[]; views: number | null };
  experiment: { id: string; status: string; evaluation?: { text: string; winner: string | null } } | null;
  direct: { leader: string | null; young: boolean; mature?: boolean; text: string } | null;
  /** Por que o experimento ainda não abriu (falta o vídeo da A, a B está privada...). */
  notice?: string | null;
};

export type AbOverview = { main: AbBranch; branches: AbBranch[]; published: AbReading[] };

export type AbSceneChange = { id: string; status: 'nova' | 'mudou' | 'saiu'; a: string | null; b: string | null };

export type AbCompare = {
  lang: string;
  a: AbBranch;
  b: AbBranch;
  segments: {
    id: string;
    index: number;
    role: string;
    status: 'igual' | 'mudou' | 'novo' | 'saiu';
    /** Mudou só na A depois que a B nasceu (a B não mexeu ali): não dá para mesclar. */
    only_a?: boolean;
    a: string | null;
    b: string | null;
    scenes_a: number;
    scenes_b: number;
    scene_changes: AbSceneChange[];
  }[];
  parts: { key: 'titulo' | 'estilo'; label: string; changed: boolean; only_a?: boolean; a: string; b: string }[];
  changed: number;
};

export type Publication = {
  id: string;
  platform: string;
  language: string;
  driver: string;
  status: string;
  package_path: string | null;
  url: string | null;
  details: {
    caption?: string;
    url?: string;
    container_id?: string;
    video_url?: string;
    account?: string;
    user_id?: string;
  };
  error: string | null;
  created_at: string | null;
};

export type ProjectDetail = {
  project: ProjectRow;
  doc: ProjectDoc;
  style: StyleSpec | null;
  warnings: string[];
  versions: Version[];
  jobs: Job[];
  outputs: { preview?: BuildResult | null; final?: BuildResult | null };
  publications: Publication[];
  glossary_warnings?: GlossaryWarning[];
};

export type StyleSpec = {
  id: string;
  name: string;
  description: string;
  family: string;
  families_mix: string[];
  structure: string;
  tags: string[];
  format: { type: 'short' | 'long'; aspect: string; resolution: string; fps: number; duration: { target_s: number; min_s: number | null; max_s: number | null } };
  languages: { primary: string; outputs: string[]; delivery: LanguageDelivery };
  script: { tone: string; structure: string[]; hook_max_s: number; fact_check: boolean; cta: string | null; banned_phrases: string[] };
  voice: { chain: string[]; voice: string | null; style_prompt: string | null; speed: number; gap_s: number; voice_profile: string | null; preview?: PreviewVoice };
  /** `languages`: linguagens visuais preferidas (a primeira dominante; vazio = livre). */
  art: {
    bible: string;
    banned: string[];
    image_chain: string[];
    thumbnail_chain: string[];
    photoreal: boolean;
    languages?: string[];
    video_chain?: string[];
    video_scenes?: number | null;
    /** Política de fontes (ADR 0004); servidor antigo: ausente. */
    sources?: SourcePolicy;
    /** OBSOLETO: vale `sources` (escondido no editor). */
    sources_mix?: Record<string, number>;
  };
  edit: {
    shot_seconds: { min: number; max: number };
    /** Fração das batidas que pode ganhar imagem nova (o resto reaproveita ou vira cartão). */
    new_image_ratio?: number;
    camera: Record<string, number>;
    transitions: Record<string, number>;
    /** Pesos do tratamento das cenas de imagem (lousa, colagem, tela). Vazio: automático pela família. */
    treatments?: Record<string, number>;
    transition_seconds: number;
    effects: { type: string; amount: number; preset?: string | null }[];
    captions: {
      style: string;
      font: string;
      weight: number;
      size: number;
      color: string;
      highlight: string;
      stroke: number;
      stroke_color: string;
      position: string;
      max_words_per_line: number;
      uppercase: boolean;
      background: string | null;
    };
    text_cards: { font: string; color: string; accent: string; background: string; animation: string };
    progress_bar: boolean;
    progress_color: string;
    watermark: string | null;
    zoom_strength: number;
    /** Avatar do apresentador nas cenas: 'auto' segue canal/projeto; 'off' desliga neste estilo. */
    presenter?: 'auto' | 'off';
    /** Gramática de edição (ADR 0004): ritmo dos planos e eventos por minuto. Servidor antigo: ausente. */
    grammar?: EditGrammar;
  };
  audio: { music: { source: string; mood: string; volume_db: number; ducking: boolean }; sfx: { auto: boolean; pack: string; volume_db: number }; loudness_lufs: number };
  thumbnail: {
    template: string;
    variations: number;
    per_language: boolean;
    font: string;
    color: string;
    accent: string;
    /* Pacote e compositor por camadas (servidor antigo: ausentes). */
    /** Templates do compositor em ordem de preferência. */
    templates?: ThumbTemplate[];
    title_candidates?: number;
    thumb_candidates?: number;
    /** Usa o avatar na thumb quando o canal o tem ligado. */
    presenter?: boolean;
    palette?: Record<string, string>;
    logo_asset?: string | null;
    /** Libera frame oficial e key art na thumb (o canal pode trocar em style_bible.thumbs). */
    allow_editorial?: boolean;
    /** Projeto longo novo para na etapa do pacote (ignorado no Short). */
    gate_package?: boolean;
  };
  [key: string]: unknown;
};

export type VoiceOption = { id: string; label: string; gender: string | null; tone: string; sample: string | null };
export type VoiceProvider = { id: string; name: string; available: boolean; reason: string; preview_only: boolean; local: boolean; voices: VoiceOption[] };
export type VoiceCatalog = { lang: string; sample_text: string; providers: VoiceProvider[] };
export type VoiceChoice = { provider: string; voice: string };
/** Voz da prévia: local e grátis (Kokoro) ou a própria voz final. */
export type PreviewVoice = { local: boolean; voice: string | null };
// `preview` falta quando o servidor ainda roda a versão anterior (antes de reiniciar o app).
/** O que a próxima prévia ou renderização vai gravar (GET /api/projects/:id/plan). */
export type BuildPlan = {
  mode: 'preview' | 'final';
  tts_missing: { segment_id: string; lang: string; chars: number }[];
  tts_chars: number;
  /** A narração que falta sai da voz local (sem cota). */
  tts_local: boolean;
  tts_provider: string | null;
  tts_voice: string | null;
  images_missing: string[];
  /** Cenas dirigidas sem direção de arte (o estilo foi trocado): só geram imagem depois de refazer as cenas. */
  direction_missing?: string[];
  /** Cenas que mostram mídia real própria (não geram imagem de IA). */
  media_scenes?: string[];
  /** Cenas com mídia real cujo arquivo sumiu da pasta de dados (ficam sem imagem). */
  media_missing?: string[];
  /** Cenas com parallax 2.5D (camadas pelo mapa de profundidade). */
  parallax_scenes?: string[];
  /** Tamanho (MB) do modelo de profundidade que ainda vai ser baixado para o parallax. */
  depth_model_mb?: number;
  /** Por que o parallax não roda agora (sem onnxruntime, modo offline sem o modelo): as cenas sem camadas guardadas usam o Ken Burns. */
  parallax_unavailable?: string;
  image_provider: string | null;
  estimated_cost_usd: number;
  estimated_seconds: number;
  /** Créditos estimados da narração (caracteres × créditos por caractere do canal); null sem configuração. */
  credits_estimate?: { credits: number; per_char: number; usd: number | null } | null;
};

/** Resposta de POST /ops e /edit/apply: `notice` diz por que a prévia não foi atualizada (job null). */
export type EditResult = { version: number; job: Job | null; notice?: string | null };
export const LOCAL_VOICE_PROVIDER = 'kokoro_local';

/** Estilo sugerido ao criar um projeto e base do "criar por descrição" (o mesmo `DEFAULT_STYLE_ID` de backend/studio/core/styles/library.py). */
export const DEFAULT_STYLE_ID = 'curiosidades-short';

export type StyleItem ={ source: 'preset' | 'user'; version: number; style: StyleSpec; warnings: string[]; parent_id?: string | null };

export type Provider = {
  id: string;
  kind: string;
  name: string;
  available: boolean;
  reason: string;
  commercial_use: boolean;
  local: boolean;
  free: boolean;
  credential_fields: string[];
  env_vars: string[];
  credentials: { fields: Record<string, string>; from_env: string | null; configured: boolean } | null;
  notes: string;
  cost_note: string;
  quota_exhausted_today: boolean;
};

export type PostingSlot = { weekdays: number[]; time: string; /** Motivo do horário (até 120 caracteres). */ reason?: string };
export type PostingPlan = { timezone: string; slots: PostingSlot[] };
/** Janelas do canal (0 desliga): faixas da biblioteca que não se repetem e vídeos que o verificador de novidade compara. */
export type ChannelNovelty = { music_window: number; window: number };
export type Channel = {
  id: string;
  name: string;
  niche: string;
  persona: string;
  languages: string[];
  default_style_id: string | null;
  posting: PostingPlan;
  novelty?: ChannelNovelty;
  /** Identidade visual (vai para o storyboard). Chave `identidade`: o texto editado na tela de canais. */
  style_bible?: Record<string, unknown>;
  /** Canal do YouTube ligado (login do Google ou link público). Tipos no bloco "Canais e YouTube", no fim. */
  youtube?: ChannelYouTube | null;
  /* Gestão do canal (ADR 0002) */
  audience?: string;
  cadence?: string;
  /** Meta de posts por dia (0 = sem meta). */
  posts_per_day?: number;
  /** Estoque mínimo de vídeos prontos (0 = sem alerta). */
  min_stock?: number;
  stock_format?: 'short' | 'long' | 'any';
  /** Hashtag no fim do título dos Shorts ("" ou "#palavra"). */
  short_hashtag?: string;
  credits_per_char?: number;
  credit_price_usd?: number;
  /** Voz final padrão do canal, pré-selecionada no Novo projeto. */
  voice_defaults?: { final?: { provider: string; voice: string } };
  /** Playlist em que os envios dos projetos deste canal entram (null: nenhuma). */
  youtube_playlist?: { id: string; title: string } | null;
  /** Dicionário de pronúncia: como a voz fala um termo (vale para todas as vozes). */
  pronunciation?: PronunciationEntry[];
  /** Glossário de tradução: termos que não se traduzem ou têm tradução fixa por idioma. */
  glossary?: GlossaryEntry[];
};
/** `lang` vazio: vale para todos os idiomas. */
/** Para quais vozes a troca vale: todas, só as locais (prévia) ou só as de API (vídeo final). */
export type PronunciationVoices = 'all' | 'local' | 'api';
export type PronunciationEntry = { term: string; say: string; lang: string; voices?: PronunciationVoices };
/** `keep`: não traduzir; `translations`: tradução fixa por idioma (vale mais que `keep`). */
export type GlossaryEntry = { term: string; keep: boolean; translations: Record<string, string>; note: string };
/** Termo do glossário que a tradução não respeitou (refeito a cada abertura do projeto). */
export type GlossaryWarning = { lang: string; kind: 'segment' | 'scene'; id: string; term: string; expected: string; message: string };
export type ChannelSlots ={ timezone: string; configured: boolean; slots: string[] };

/** GET /api/projects/{id}/novelty: imagens, miniatura e título parecidos com os últimos vídeos do canal (só aviso). */
export type NoveltyReport = {
  enabled: boolean;
  /** O projeto já tem prévia ou vídeo final (sem isso, só os títulos são comparados). */
  ready: boolean;
  window: number;
  reason?: string;
  compared: { project_id: string; title: string }[];
  images: {
    total: number;
    share: number;
    repeated: { scene: string; distance: number; project_id: string; project_title: string }[];
  };
  thumbnail: { project_id: string; project_title: string; distance: number } | null;
  titles: { title: string; similarity: number; source: 'projeto' | 'youtube'; project_id?: string; video_id?: string }[];
  /** Vídeos do canal sem impressão digital em dia (renderizados antes do verificador): entram depois da próxima renderização deles. */
  pending?: { project_id: string; title: string }[];
  warnings: string[];
};
/** Personagem fixo (StyleBible do canal ou do projeto). O nome serve só para achar o personagem na narração: o
 * prompt da imagem usa a descrição (`look`) e o gerador que aceita referência recebe as imagens âncora. */
export type Character = {
  id: string;
  name: string;
  aliases: string[];
  /** Descrição visual fixa, em inglês. */
  look: string;
  /** Imagens âncora (até 3), caminhos relativos na pasta de dados (use com `fileUrl`). */
  images: string[];
};
export type ProjectCharacters = {
  channel_id: string | null;
  project: Character[];
  channel: Character[];
  /** Os que valem no projeto: os do projeto e os do canal que o projeto não trocou (mesmo id). */
  effective: Character[];
};
export const CHARACTER_MAX_IMAGES = 3;

export type AgendaItem = {
  id: string;
  project_id: string;
  project_title: string;
  channel_id: string | null;
  channel_name: string;
  platform: string;
  language: string;
  driver: string;
  status: string;
  scheduled_at: string | null;
  created_at: string | null;
  published_at: string | null;
  url: string | null;
  package_path: string | null;
  caption: string | null;
  upload_url: string | null;
  privacy: string | null;
};
export type Agenda = { now: string; upcoming: AgendaItem[]; manual: AgendaItem[]; recent: AgendaItem[] };
export type YouTubeReady = {
  project_id: string;
  title: string;
  lang: string;
  channel_id: string | null;
  channel_name: string;
  duration: number | null;
  preview_only: boolean;
};
export type YouTubeBatchResult = {
  project_id: string;
  lang: string;
  title?: string;
  channel_name?: string;
  publish_at?: string;
  job_id?: string;
  error?: string;
};

export const WEEKDAYS = ['seg', 'ter', 'qua', 'qui', 'sex', 'sáb', 'dom'];
export const TIMEZONES = [
  'America/Sao_Paulo',
  'America/Manaus',
  'America/Fortaleza',
  'America/Mexico_City',
  'America/New_York',
  'America/Chicago',
  'America/Denver',
  'America/Los_Angeles',
  'Europe/Lisbon',
  'Europe/London',
  'Europe/Madrid',
  'Europe/Berlin',
  'Asia/Kolkata',
  'Asia/Tokyo',
  'UTC',
];
export const PLATFORM_LABELS: Record<string, string> = {
  youtube: 'YouTube',
  tiktok: 'TikTok',
  instagram: 'Instagram Reels',
  facebook: 'Facebook Reels',
  threads: 'Threads',
  x: 'X',
};

/** Data e hora curtas em pt-BR (no fuso do navegador, ou no fuso indicado). */
export const formatWhen = (iso: string, timeZone?: string) =>
  new Date(iso).toLocaleString('pt-BR', { weekday: 'short', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', timeZone });

/** Valor para <input type="datetime-local"> (hora local do navegador). */
export const toLocalInput = (iso: string) => {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
};

export type EditProposal = {
  summary: string;
  ops: Record<string, unknown>[];
  estimate: BuildPlan | null;
  provider: string;
  base_version: number;
};

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/** Mensagem mostrada quando o servidor local não responde (fetch rejeitado). */
export const NETWORK_ERROR_MESSAGE = 'Não foi possível falar com o servidor local. Ele está rodando?';

type ValidationIssue = { loc?: (string | number)[]; msg?: string };

/** Transforma o `detail` do FastAPI em texto legível (422 vira linhas "campo: mensagem"). */
const formatDetail = (detail: unknown): string => {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item: ValidationIssue | string) => {
        if (typeof item === 'string') return item;
        const loc = (item.loc ?? []).filter((part) => part !== 'body' && part !== 'query' && part !== 'path');
        const field = loc.length ? String(loc[loc.length - 1]) : '';
        const msg = item.msg ?? JSON.stringify(item);
        return field ? `${field}: ${msg}` : msg;
      })
      .join('\n');
  }
  if (detail && typeof detail === 'object') {
    const obj = detail as Record<string, unknown>;
    if (typeof obj.message === 'string') return obj.message;
    if (typeof obj.detail === 'string') return obj.detail;
  }
  return JSON.stringify(detail);
};

/** Mensagens padrão (em inglês) do servidor HTTP traduzidas. */
const HTTP_MESSAGES: Record<string, string> = {
  'Not Found': 'Não encontrado no servidor. Se você atualizou o app, reinicie o servidor (scripts\\iniciar.cmd).',
  'Method Not Allowed': 'O servidor não aceita esta ação. Reinicie o servidor se você atualizou o app.',
  'Internal Server Error': 'Erro interno do servidor. Veja a janela do terminal para os detalhes.',
  'Bad Gateway': NETWORK_ERROR_MESSAGE,
  'Service Unavailable': NETWORK_ERROR_MESSAGE,
  'Gateway Timeout': NETWORK_ERROR_MESSAGE,
};

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response;
  // FormData (envio de arquivo) vai como multipart: o navegador põe o Content-Type com o boundary.
  const form = typeof FormData !== 'undefined' && body instanceof FormData;
  try {
    res = await fetch(path, {
      method,
      headers: body === undefined || form ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : form ? (body as FormData) : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, NETWORK_ERROR_MESSAGE);
  }
  if (!res.ok) {
    let text = '';
    try {
      text = await res.text();
    } catch {
      /* corpo ilegível */
    }
    // No modo dev, o proxy do Vite responde 5xx com corpo vazio quando o backend está fora do ar.
    if (res.status >= 500 && !text.trim()) throw new ApiError(res.status, NETWORK_ERROR_MESSAGE);
    let detail = res.statusText || `Erro ${res.status}`;
    try {
      const data = JSON.parse(text);
      detail = formatDetail(data?.detail ?? data);
    } catch {
      /* resposta sem JSON */
    }
    throw new ApiError(res.status, HTTP_MESSAGES[detail] ?? detail);
  }
  return (await res.json()) as T;
}

export const api = {
  get: <T,>(path: string) => request<T>('GET', path),
  post: <T,>(path: string, body?: unknown) => request<T>('POST', path, body ?? {}),
  put: <T,>(path: string, body?: unknown) => request<T>('PUT', path, body ?? {}),
  patch: <T,>(path: string, body?: unknown) => request<T>('PATCH', path, body ?? {}),
  del: <T,>(path: string) => request<T>('DELETE', path),
  /** POST multipart (envio de arquivo). */
  upload: <T,>(path: string, form: FormData) => request<T>('POST', path, form),
};

export const fileUrl = (rel: string | null | undefined) => (rel ? `/files/${rel.replace(/^\/+/, '')}` : '');

export const LANGUAGES: { code: string; label: string }[] = [
  { code: 'pt-BR', label: 'Português (Brasil)' },
  { code: 'en-US', label: 'Inglês (EUA)' },
  { code: 'es-ES', label: 'Espanhol (Espanha)' },
  { code: 'es-MX', label: 'Espanhol (México)' },
  { code: 'fr-FR', label: 'Francês' },
  { code: 'de-DE', label: 'Alemão' },
  { code: 'it-IT', label: 'Italiano' },
  { code: 'ja-JP', label: 'Japonês' },
  { code: 'ko-KR', label: 'Coreano' },
  { code: 'hi-IN', label: 'Hindi' },
  { code: 'ar-SA', label: 'Árabe' },
  { code: 'ru-RU', label: 'Russo' },
  { code: 'id-ID', label: 'Indonésio' },
  { code: 'tr-TR', label: 'Turco' },
];

export const STATUS_LABELS: Record<string, string> = {
  draft: 'Rascunho',
  package_review: 'Escolher título e thumb',
  script_review: 'Roteiro para revisar',
  storyboard: 'Storyboard pronto',
  storyboard_review: 'Cenas para conferir',
  preview_ready: 'Prévia pronta',
  rendered: 'Vídeo renderizado',
};

export const JOB_LABELS: Record<string, string> = {
  script: 'Roteiro',
  storyboard: 'Storyboard',
  translate: 'Tradução',
  metadata: 'Metadados',
  preview: 'Prévia',
  render: 'Render final',
  autopilot: 'Piloto automático',
  publish_youtube: 'Envio ao YouTube',
  metrics_collect: 'Coleta de métricas',
  shorts_suggest: 'Sugestão de Shorts',
  routine_morning: 'Rotina da manhã',
  backup: 'Cópia do banco',
  // Qualidade e pesquisa (ADR 0003)
  radar_collect: 'Radar de nichos',
  factcheck: 'Checagem de fatos',
  music_curate: 'Curadoria das trilhas',
  sfx_import_canais: 'Efeitos do canais',
  sfx_voice: 'Interjeições na voz do canal',
  sfx_scan: 'Efeitos soltos na pasta',
  audio_check: 'Conferência do áudio',
  final_check: 'Conferência do vídeo final',
  page_capture: 'Captura de página',
  reference_shots: 'Planos do vídeo de referência',
  reference_profile: 'Perfil de edição da referência',
  thumbs_variants: 'Miniaturas para o teste A/B',
  publish_instagram: 'Publicação no Instagram',
  ab_build: 'Versões A/B',
  media_find: 'Busca de mídia oficial',
  video_research: 'Pesquisa de vídeos',
};

/* ------------------------------------------------------------------ */
/* Canais e YouTube (vincular canal já existente)                      */
/* ------------------------------------------------------------------ */

/** ok: token do canal; global: publica pela conta da aba de publicação; error: o Google recusou o token;
 *  public: ligado só pelo link (não publica); missing: desvinculado. */
export type ChannelTokenStatus = 'ok' | 'global' | 'error' | 'public' | 'missing';

export type ChannelVideo = { title: string; video_id: string; published: string };

/** `settings.youtube` do canal, como o servidor manda para a tela. */
export type ChannelYouTube = {
  channel_id: string;
  title: string;
  handle: string;
  description: string;
  url: string;
  /** Foto baixada para a pasta de dados (use com `fileUrl`). */
  avatar: string | null;
  country: string;
  language: string | null;
  keywords: string[];
  subscribers: number | null;
  videos: number | null;
  views: number | null;
  recent_videos: ChannelVideo[];
  source: 'oauth' | 'public';
  imported_at: string;
  token_error: string | null;
  token: ChannelTokenStatus;
};

/** GET /api/youtube/status (campos novos opcionais: o servidor antigo não manda). */
export type YouTubeAccountStatus = {
  client_configured: boolean;
  connected: boolean;
  channel_title: string;
  channel_id: string;
  global_connected?: boolean;
  /** `revenue`: o canal tem a receita do YouTube ligada (escolhida ao vincular). */
  linked?: { channel_id: string; channel_title: string; revenue?: boolean }[];
  /** Todas as contas que podem publicar (global e canais vinculados, sem repetir o mesmo canal). */
  accounts?: YouTubeAccount[];
  /** Com project_id: o canal do projeto não está vinculado, então o envio pode escolher a conta. */
  can_choose_account?: boolean;
  /** Com project_id: id no YouTube da conta que o projeto usa sem escolha ("" se não houver). */
  account_channel_id?: string;
  /** Com project_id: playlist padrão do canal do projeto. */
  default_playlist?: { id: string; title: string } | null;
  reason?: string | null;
};

export type YouTubeAccount = {
  channel_id: string;
  channel_title: string;
  /** Conta conectada na aba de publicação (a "global"). */
  global: boolean;
  /** Canais do Vulto ligados a este canal do YouTube. */
  vulto_channels: string[];
};

/** GET /api/youtube/playlists */
export type YouTubePlaylist = { id: string; title: string; privacy: string; count: number | null };
export type YouTubePlaylists = {
  account_title: string;
  playlists: YouTubePlaylist[];
  /** O canal tem mais playlists do que o app lê (`max`): as que sobraram não aparecem. */
  truncated?: boolean;
  max?: number;
};

/** GET /api/youtube/quota: cota da YouTube Data API usada hoje (dia do Pacífico). */
export type YouTubeQuota = {
  day: string;
  resets_at: string;
  limit: number;
  custom_limit: boolean;
  used: number;
  remaining: number;
  uploads: number;
  upload_limit: number;
  custom_upload_limit: boolean;
  uploads_remaining: number;
  exhausted: boolean;
  /** Cota geral (10.000) acabou: o vídeo sobe, mas capa, legendas e playlist falham. Só avisa. */
  general_over: boolean;
  /** Cota de envios de vídeo acabou: nenhum vídeo sobe hoje. Bloqueia o envio. */
  uploads_over: boolean;
  level: 'ok' | 'near' | 'over';
  message: string | null;
  /** Unidades que um envio típico gasta (capa + legendas). */
  publish_estimate: number;
  by_method: { method: string; calls: number; units: number }[];
};

/** POST /api/youtube/link */
export type YouTubeLinkStart = { auth_url: string; state: string };
/** GET /api/youtube/link/:state */
export type YouTubeLinkStatus = {
  status: 'pending' | 'done' | 'error' | 'unknown';
  channel_title?: string;
  vulto_channel_id?: string;
  created?: boolean;
  warnings?: string[];
  error?: string;
};

/** POST /api/channels/:id/suggest: proposta da IA (nada é salvo até o usuário revisar). */
export type ChannelSuggestion = {
  niche: string;
  persona: string;
  languages: string[];
  default_style_id: string | null;
  identity: string;
  reason: string;
  provider: string;
};

/* ------------------------------------------------------------------ */
/* Instagram nativo (Reels pela API oficial)                           */
/* ------------------------------------------------------------------ */

/** Conta profissional conectada (o token fica só no servidor). */
export type InstagramAccount = {
  user_id: string;
  username: string;
  account_type: string;
  expires_at: string | null;
  /** Token colado do painel: a validade é estimada em 60 dias. */
  expires_estimated: boolean;
};

/** native: publica pela API oficial; manual: usa o pacote manual. */
export type InstagramDriver = 'native' | 'manual';

/** GET /api/instagram/status (com project_id vêm os campos do projeto). */
export type InstagramStatus = {
  app_configured: boolean;
  accounts: InstagramAccount[];
  redirect_uri: string;
  default_redirect_uri: string;
  public_base_url: string;
  channel_id?: string | null;
  driver?: InstagramDriver;
  channel_user_id?: string | null;
  ready?: boolean;
  account?: InstagramAccount | null;
  reason?: string | null;
};

/** GET /api/instagram/accounts/:id */
export type InstagramAccountDetail = InstagramAccount & {
  limit: { used: number; total: number; window_s: number };
};

/** POST /api/instagram/link */
export type InstagramLinkStart = { auth_url: string; state: string; redirect_uri: string };
/** GET /api/instagram/link/:state */
export type InstagramLinkStatus = {
  status: 'pending' | 'done' | 'error' | 'unknown';
  username?: string;
  error?: string;
};
