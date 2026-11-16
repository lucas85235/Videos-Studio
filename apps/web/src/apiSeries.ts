/* Cliente das séries com piloto automático (episódios até o portão de revisão; nunca publica). */
import { api } from './api';

export type SeriesFormat = '' | 'short' | 'long';
/** list: fila de temas da série; ideas: ideias aprovadas do canal (nota mais alta primeiro). */
export type SeriesSource = 'list' | 'ideas';
/** parado: o job foi cancelado (ou terminou sem chegar a uma revisão); espera você tentar de novo ou apagar. */
export type EpisodeState = 'producao' | 'revisao' | 'pronto' | 'falhou' | 'parado' | 'publicado' | 'apagado';

export type SeriesEpisode = {
  id: number;
  series_id: string;
  number: number;
  project_id: string;
  /** null quando o projeto foi apagado. */
  project_title: string | null;
  project_status: string | null;
  topic: string;
  idea_id: string | null;
  job_id: string | null;
  job_status: string | null;
  error: string;
  origin: 'auto' | 'manual';
  state: EpisodeState;
  state_label: string;
  /** Em produção, esperando revisão, pronto, com falha ou parado: conta no estoque da série. */
  open: boolean;
  created_at: string | null;
};

export type SeriesCounts = Record<EpisodeState, number> & { open: number; total: number };

export type Series = {
  id: string;
  channel_id: string;
  channel_name: string;
  name: string;
  theme: string;
  notes: string;
  format: SeriesFormat;
  style_id: string | null;
  cadence_days: number;
  stock_target: number;
  max_per_run: number;
  source: SeriesSource;
  topics: string[];
  idea_filter: string;
  autopilot: boolean;
  active: boolean;
  last_run_at: string | null;
  last_run_message: string;
  created_at: string | null;
  counts: SeriesCounts;
  below: boolean;
  coverage_days: number;
  next_number: number;
  /** Só no detalhe (GET /api/series/{id}). */
  episodes?: SeriesEpisode[];
};

export type SeriesInput = {
  channel_id: string;
  name: string;
  theme?: string;
  notes?: string;
  format?: SeriesFormat;
  style_id?: string | null;
  cadence_days?: number;
  stock_target?: number;
  max_per_run?: number;
  source?: SeriesSource;
  topics?: string[];
  idea_filter?: string;
  autopilot?: boolean;
  active?: boolean;
};

export type SeriesRunResult = {
  created: { number: number; project_id: string; topic: string; idea_id: string | null; job_id: string | null }[];
  message: string;
  skipped: boolean;
  error?: boolean;
  series: Series;
};

/** Resumo de uma série na página Hoje. */
export type SeriesToday = {
  id: string;
  name: string;
  channel_id: string;
  channel_name: string;
  autopilot: boolean;
  open: number;
  stock_target: number;
  below: boolean;
  review: number;
  ready: number;
  failed: number;
  /** Episódios parados (job cancelado): esperam você tentar de novo ou apagar. */
  stopped: number;
  in_production: number;
  coverage_days: number;
  /** Temas que ainda restam na fila (null quando a série usa as ideias). */
  topics_left: number | null;
  source: SeriesSource;
  last_run_at: string | null;
  last_run_message: string;
};

export const SERIES_KEY = ['series'] as const;

export const seriesApi = {
  list: (channelId?: string | null) => api.get<Series[]>(`/api/series${channelId ? `?channel_id=${encodeURIComponent(channelId)}` : ''}`),
  get: (id: string) => api.get<Series>(`/api/series/${id}`),
  create: (body: SeriesInput) => api.post<Series>('/api/series', body),
  /** `topics_base`: a fila que o painel leu. Se ela mudou desde então, salvar `topics` dá 409. */
  update: (id: string, body: Partial<SeriesInput> & { topics_base?: string[] }) => api.put<Series>(`/api/series/${id}`, body),
  remove: (id: string) => api.del<{ deleted: boolean }>(`/api/series/${id}`),
  run: (id: string, count?: number) => api.post<SeriesRunResult>(`/api/series/${id}/run`, count ? { count } : {}),
};

export const CADENCE_OPTIONS: { days: number; label: string }[] = [
  { days: 1, label: 'Todo dia' },
  { days: 2, label: 'A cada 2 dias' },
  { days: 3, label: 'Duas vezes por semana' },
  { days: 7, label: 'Toda semana' },
  { days: 14, label: 'A cada 2 semanas' },
  { days: 30, label: 'Todo mês' },
];

export const cadenceLabel = (days: number) => CADENCE_OPTIONS.find((o) => o.days === days)?.label ?? `A cada ${days} dias`;
