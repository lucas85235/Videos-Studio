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
};

export type SceneCardItem = { label: LocalizedText; value: LocalizedText };

/** Cartão (número, comparação) e sobreposições (faixa, carimbo, fonte) da cena. */
export type SceneCard = {
  value: LocalizedText;
  label: LocalizedText;
  items: SceneCardItem[];
  highlight: number | null;
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
  counts: { scenes: number; new_images: number; reused: number; cards: number; budget: number; est_seconds: number };
  issues: StoryboardIssue[];
  retried: boolean;
  style_fit: 'ok' | 'fraco' | 'errado';
  style_note: string;
  suggested_style: string | null;
  provider?: string;
  version?: number;
};

/** Paradas do piloto automático (projeto sem a chave: script e prévia valem true; storyboard vale false). */
export type ProjectGates = { script?: boolean; storyboard?: boolean; preview?: boolean; publish?: boolean; [k: string]: boolean | undefined };

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
};

export type BuildResult = {
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

export type Version = { number: number; parent: number | null; message: string; author: string; created_at: string | null };

export type Publication = {
  id: string;
  platform: string;
  language: string;
  driver: string;
  status: string;
  package_path: string | null;
  url: string | null;
  details: { caption?: string; url?: string };
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
  languages: { primary: string; outputs: string[]; delivery: string };
  script: { tone: string; structure: string[]; hook_max_s: number; fact_check: boolean; cta: string | null; banned_phrases: string[] };
  voice: { chain: string[]; voice: string | null; style_prompt: string | null; speed: number; gap_s: number; voice_profile: string | null; preview?: PreviewVoice };
  /** `languages`: linguagens visuais preferidas (a primeira dominante; vazio = livre). */
  art: { bible: string; banned: string[]; image_chain: string[]; thumbnail_chain: string[]; photoreal: boolean; languages?: string[] };
  edit: {
    shot_seconds: { min: number; max: number };
    /** Fração das batidas que pode ganhar imagem nova (o resto reaproveita ou vira cartão). */
    new_image_ratio?: number;
    camera: Record<string, number>;
    transitions: Record<string, number>;
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
  };
  audio: { music: { source: string; mood: string; volume_db: number; ducking: boolean }; sfx: { auto: boolean; pack: string; volume_db: number }; loudness_lufs: number };
  thumbnail: { template: string; variations: number; per_language: boolean; font: string; color: string; accent: string };
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
  image_provider: string | null;
  estimated_cost_usd: number;
  estimated_seconds: number;
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

export type PostingSlot = { weekdays: number[]; time: string };
export type PostingPlan = { timezone: string; slots: PostingSlot[] };
export type Channel = {
  id: string;
  name: string;
  niche: string;
  persona: string;
  languages: string[];
  default_style_id: string | null;
  posting: PostingPlan;
};
export type ChannelSlots = { timezone: string; configured: boolean; slots: string[] };

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
  try {
    res = await fetch(path, {
      method,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
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
};
