/* Cliente do bloco E (ADR 0003): radar de nichos e vídeos fora da curva (YouTube Data API v3). */
import { api, type Job } from './api';
import type { Idea } from './apiBusiness';

export type RadarMarket = 'us' | 'br';
export type RadarVerdict = 'Modelar' | 'Observar' | 'Estudar o viral';

export const MARKET_LABELS: Record<RadarMarket, string> = { us: 'EUA', br: 'BR' };

export type RadarAccess = { ok: boolean; source: string; message: string; key_masked: string };

/** Um nicho do catálogo (mesmo formato do `nichos.toml`). */
export type RadarCatalogNiche = {
  id: string;
  nome: string;
  cat: string;
  duracao: 'medium' | 'long';
  en: string[];
  pt: string[];
};

/** Par [texto, explicação] (`evitar` e `alertas`). */
export type RadarPair = [string, string];

export type RadarCatalog = {
  radar: {
    orcamento: number;
    dias: number;
    hit_us: number;
    hit_br: number;
    novo_meses: number;
    candidatos: number;
    evitar: RadarPair[];
    alertas: RadarPair[];
  };
  rpm: Record<string, { rpm: number; pequena: boolean }>;
  nichos: RadarCatalogNiche[];
  /** O catálogo foi editado (senão vale o `nichos.toml` do Vulto). */
  custom?: boolean;
};

export type RadarRun = {
  id: number;
  created_at: string | null;
  source: string;
  searches: number;
  units: number;
  videos: number;
  channels: number;
  skipped: string[];
  status: 'ok' | 'falhou';
  error: string | null;
  params: Record<string, unknown>;
};

export type RadarState = {
  access: RadarAccess;
  auto: boolean;
  last_week: string | null;
  last_run: RadarRun | null;
  last_job: string | null;
  catalog: { niches: number; markets: RadarMarket[]; custom: boolean };
};

export type RadarSimulation = {
  buscas: number;
  unidades_previstas: number;
  orcamento: number;
  pulados: string[];
  /** Primeira busca (caminho da API), para conferir. */
  exemplo: string | null;
};

export type RadarCollectInput = { niches?: string[]; markets?: RadarMarket[]; budget?: number };

/** Vídeo recente de um canal: id, título, views (null = escondidas), idade em dias, duração em min. */
export type RadarRecentVideo = { id: string; t: string; v: number | null; a: number; l: number };

/** Saída de `recentes` do canal. */
export type RadarRecent = {
  latest?: RadarRecentVideo[];
  med?: number;
  hits?: number;
  settled?: number;
  up30?: number;
  typLen?: number;
  verdict?: RadarVerdict;
  format?: string;
  risk?: string;
};

export type RadarPanelNiche = {
  name: string;
  niche: string;
  mk: RadarMarket;
  q: string[];
  n: number;
  med: number;
  p75: number;
  h100: number;
  h500: number;
  newShare: number;
  newCh: number;
  bestNew: number;
  subs: number;
  len: number;
  cat: string;
  rpm: number;
  small: boolean;
};

export type RadarPanelChannel = RadarRecent & {
  id: string;
  name: string;
  url: string;
  country: string | null;
  joined: string | null;
  ageM: number | null;
  subs: number | null;
  views: number;
  videos: number;
  niches: string[];
  sample: string[];
  best: string | null;
  bestViews: number;
  vpm: number;
  flag: string | null;
};

/** Vídeo no painel: id, título, views, canal, idade em dias, duração em min, nichos. */
export type RadarPanelVideo = { id: string; t: string; v: number; c: string | null; a: number | null; l: number; n: string[] };

export type RadarArbitrage = {
  id: string;
  nome: string;
  en: { med: number; h100: number; new_hit: number; n: number };
  pt: { med: number; n10k: number; best: number; new_share: number; n: number };
  leitura: 'Aberto em PT' | 'Concorrido em PT' | 'Morno';
  score: number;
};

export type RadarRec = { n: string; mk: RadarMarket; id: string; t: string };

export type RadarPanel = {
  meta: {
    run_id: number;
    captured: string;
    searches: number;
    units: number;
    videos: number;
    channels: number;
    source: string;
    description: string;
    method: string[];
    limits: string[];
    skipped: string[];
    hit_us: number;
    hit_br: number;
  };
  niches: RadarPanelNiche[];
  channels: RadarPanelChannel[];
  videos: RadarPanelVideo[];
  arb: RadarArbitrage[];
  recs: RadarRec[];
  avoid: RadarPair[];
  rpm: Record<string, { rpm: number; pequena: boolean }>;
  facts: { label: string; text: string; url: string }[];
  markets: { country: string; rpm: number }[];
  markets_source: string;
  rpm_source: string;
};

export type RadarIdeaInput = {
  run_id: number;
  youtube_id?: string;
  channel_youtube_id?: string;
  channel_id?: string | null;
};

export const radarApi = {
  state: () => api.get<RadarState>('/api/radar'),
  setKey: (api_key: string) => api.put<{ ok: boolean; masked: string }>('/api/radar/key', { api_key }),
  deleteKey: () => api.del<{ ok: boolean }>('/api/radar/key'),
  catalog: () => api.get<RadarCatalog>('/api/radar/catalog'),
  saveCatalog: (catalog: RadarCatalog) => api.put<RadarCatalog>('/api/radar/catalog', catalog),
  resetCatalog: () => api.post<RadarCatalog>('/api/radar/catalog/reset'),
  simulate: (body: RadarCollectInput = {}) => api.post<RadarSimulation>('/api/radar/simulate', body),
  collect: (body: RadarCollectInput = {}) => api.post<Job>('/api/radar/collect', body),
  runs: () => api.get<RadarRun[]>('/api/radar/runs'),
  panel: (runId: number) => api.get<RadarPanel>(`/api/radar/runs/${runId}/panel`),
  reprocess: (runId: number) => api.post<RadarRun>(`/api/radar/runs/${runId}/reprocess`),
  report: () => api.get<{ lines: string[] }>('/api/radar/report'),
  createIdea: (body: RadarIdeaInput) => api.post<{ idea: Idea; created: boolean }>('/api/radar/ideas', body),
  settings: (auto: boolean) => api.put<RadarState>('/api/radar/settings', { auto }),
};
