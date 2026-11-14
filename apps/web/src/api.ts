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

export type Scene = {
  id: string;
  segment_id: string;
  part: number;
  parts: number;
  kind: string;
  visual: { type: string; prompt: string; query: string | null; asset: string | null; seed: number; variation: number };
  overlay_text: Record<string, string>;
  number: number | null;
  motion: string | null;
  transition_in: string | null;
  look: SceneLook;
  sfx: string | null;
  locked: boolean;
};

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
};

export type ProjectRow = {
  id: string;
  title: string;
  channel_id: string | null;
  style_id: string;
  status: string;
  current_version: number;
  settings: { gates?: Record<string, boolean>; last_preview?: BuildResult; last_final?: BuildResult; [k: string]: unknown };
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
};

export type LibraryTrack = { path: string; name: string; mood: string; size: number };
export type LibraryMusic = { dir: string; tracks: LibraryTrack[]; counts: Record<string, number>; moods: string[] };

export const MOOD_LABELS: Record<string, string> = {
  drone: 'ambiente / drone',
  tension: 'tensão',
  horror: 'terror',
  mystery: 'mistério',
  epic: 'épico',
  pulse: 'pulso',
  calm: 'calmo',
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
  voice: { chain: string[]; voice: string | null; style_prompt: string | null; speed: number; gap_s: number; voice_profile: string | null };
  art: { bible: string; banned: string[]; image_chain: string[]; thumbnail_chain: string[]; photoreal: boolean };
  edit: {
    shot_seconds: { min: number; max: number };
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

export type StyleItem = { source: 'preset' | 'user'; version: number; style: StyleSpec; warnings: string[]; parent_id?: string | null };

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
  estimate: { tts_missing: unknown[]; tts_chars: number; images_missing: string[]; estimated_cost_usd: number; estimated_seconds: number; tts_provider: string | null; image_provider: string | null } | null;
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

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail ?? data);
    } catch {
      /* resposta sem JSON */
    }
    throw new ApiError(res.status, detail);
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
};
