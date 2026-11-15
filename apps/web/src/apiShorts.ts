/* Cliente do bloco B (ADR 0002): Shorts cortados do vídeo longo (projeto derivado). */
import { api, type Job } from './api';

/** Tipo do Short (lista e iceberg têm teto de 45 s; os outros, 35 s). */
export type ShortKind = 'normal' | 'lista' | 'iceberg';

/** Trecho contínuo do longo: [primeiro segmento, último segmento]. */
export type ShortRange = [string, string];

/** Segmento do longo com a duração da narração (da timeline do vídeo ou da prévia). */
export type ShortSegment = {
  id: string;
  index: number;
  text: string;
  role: string;
  chapter: string | null;
  chapter_start: boolean;
  start_s: number | null;
  duration_s: number | null;
  scenes: { id: string; kind: string; src: string | null }[];
};

/** Sugestão de corte (job `shorts_suggest`). */
export type ShortSuggestion = {
  ranges: ShortRange[];
  segment_ids: string[];
  title: string;
  hook: string;
  tipo: ShortKind;
  duration_s: number;
  reason?: string;
  warnings: string[];
};

/** Short já cortado deste longo (projeto filho). */
export type ShortChild = {
  project_id: string;
  title: string;
  status: string;
  duration_s: number | null;
  warnings: string[];
  tipo: ShortKind;
  ranges: ShortRange[];
  updated_at: string | null;
};

export type ShortLimits = { target: [number, number]; max_s: number; max_list_s: number; title_max: number };

export type ShortOf = {
  project_id: string;
  ranges: ShortRange[];
  tipo: ShortKind;
  hook: string;
  duration_s: number;
  warnings: string[];
  lang: string;
};

export type ShortsOfProject = {
  lang: string | null;
  segments: ShortSegment[];
  /** De onde vieram as durações: vídeo final ou prévia. */
  source: 'final' | 'preview' | null;
  /** Segmentos sem a narração final no cache (prévia com a voz de teste): o render do Short grava a voz. */
  voice_missing?: string[];
  /** Cenas com imagem de teste no longo: o Short gera a imagem dele em vez de reaproveitar. */
  images_redo?: string[];
  /** A timeline é de uma versão anterior do projeto. */
  stale: boolean;
  gap_s: number;
  lead_in_s: number;
  tail_out_s: number;
  /** Por que ainda não dá para cortar (ex.: sem prévia). */
  blocker: string | null;
  suggestions: { lang: string; items: ShortSuggestion[]; provider: string; created_at: string } | null;
  children: ShortChild[];
  /** No Short: o longo de onde ele foi cortado. */
  parent: { project_id: string; title: string; exists: boolean } | null;
  short_of: ShortOf | null;
  hashtag: string;
  limits: ShortLimits;
};

export type ShortCreateInput = { ranges: ShortRange[]; title: string; hook: string; tipo: ShortKind; lang?: string };
export type ShortCreateResult = { project_id: string; duration_s: number; warnings: string[] };
export type ShortCheckInput = { title: string; duration_s: number; tipo: ShortKind; channel_id?: string | null };
export type ShortCheckResult = { warnings: string[]; final_title: string };

const q = (lang?: string) => (lang ? `?lang=${encodeURIComponent(lang)}` : '');

export const shortsApi = {
  list: (projectId: string, lang?: string) => api.get<ShortsOfProject>(`/api/projects/${projectId}/shorts${q(lang)}`),
  suggest: (projectId: string, lang?: string) => api.post<Job>(`/api/projects/${projectId}/shorts/suggest`, lang ? { lang } : {}),
  create: (projectId: string, body: ShortCreateInput) => api.post<ShortCreateResult>(`/api/projects/${projectId}/shorts`, body),
  check: (body: ShortCheckInput) => api.post<ShortCheckResult>('/api/shorts/check', body),
};
