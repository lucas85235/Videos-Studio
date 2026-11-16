/* Cliente do bloco A (ADR 0002): métricas dos vídeos, experimentos e lições. Rotas em /api/analytics/*. */
import { api, type Job } from './api';

export type MetricSource = 'youtube_api' | 'youtube_analytics' | 'manual' | 'public';

/** Uma linha de métricas (um vídeo num dia). Campos vazios = número que ainda não chegou (nunca 0 inventado). */
export type VideoMetric = {
  youtube_id: string;
  day: string;
  publication_id: string | null;
  project_id: string | null;
  channel_id: string | null;
  age_hours: number | null;
  views: number | null;
  likes: number | null;
  comments: number | null;
  impressions: number | null;
  ctr: number | null;
  avg_view_s: number | null;
  pct_watched: number | null;
  subscribers_gained: number | null;
  revenue_usd: number | null;
  rpm: number | null;
  source: MetricSource;
  updated_at?: string | null;
};

/** Vídeo na tabela de desempenho: dados do vídeo, variáveis (hora, dia, faixa de duração...) e a última métrica. */
export type AnalyticsVideo = {
  youtube_id: string;
  title: string | null;
  project_id: string | null;
  channel_id: string | null;
  published_at: string | null;
  duration_s: number | null;
  score: number | null;
  format: 'short' | 'long';
  variables: Record<string, string>;
  metric: VideoMetric | null;
  views_per_day: number | null;
};

export type VariableGroup = { value: string; n: number; median: number | null };
export type VariableResult = { variable: string; label: string; groups: VariableGroup[] };

export type Suggestion = { kind: 'Resultado' | 'Alerta' | 'Calibração' | string; text: string; variable?: string | null; experiment_id?: string | null };

/** Estado da coleta de um canal. `sem_escopo`: o token não lê o YouTube Analytics (vincular de novo). */
export type CollectState = {
  channel_id: string;
  name: string;
  state: 'ok' | 'sem_escopo' | 'sem_token' | 'publico' | 'erro' | 'nunca';
  scope: 'ok' | 'missing' | null;
  last_run: string | null;
  error: string;
  /** Impressões e CTR (Reporting API); null = ainda não tentou. */
  reach?: 'ok' | 'esperando' | 'sem_escopo' | 'erro' | null;
  reach_error?: string;
  /** Receita do YouTube (escopo opcional escolhido ao vincular); null = ainda não tentou. */
  revenue?: 'ok' | 'desligada' | 'sem_escopo' | 'erro' | null;
  revenue_error?: string;
};

export type AnalyticsResults = {
  shorts_n: number;
  overall_median: number | null;
  variables: VariableResult[];
  calibration: { n: number; rho: number | null; min: number | null; max: number | null; cut: number | null };
  longs: { youtube_id: string; title: string | null; views: number | null; days: number | null }[];
  min_age_h: number;
};

export type AnalyticsOverview = {
  channel_id: string | null;
  collect: CollectState[];
  videos: AnalyticsVideo[];
  results: AnalyticsResults;
  suggestions: Suggestion[];
  /** Experimentos abertos, cada um com a leitura atual. */
  experiments: Experiment[];
};

export type ExperimentStatus = 'aberto' | 'concluido' | 'abandonado';
export type ExperimentMetric = 'views' | 'views_dia' | 'ctr' | 'retencao' | 'avd_s' | 'likes';

export const METRIC_LABELS: Record<ExperimentMetric, string> = {
  views: 'Views',
  views_dia: 'Views por dia',
  ctr: 'CTR (%)',
  retencao: 'Retenção (%)',
  avd_s: 'Duração média vista (s)',
  likes: 'Likes',
};

export type ExperimentEvaluation = {
  text: string;
  winner: string | null;
  young: number;
  variants: { variant: string; n: number; median: number | null }[];
  variable?: string;
  metric?: string;
};

export type Experiment = {
  id: string;
  channel_id: string | null;
  hypothesis: string;
  variable: string;
  variants: string[];
  metric: ExperimentMetric;
  project_ids: string[];
  status: ExperimentStatus;
  result: string;
  learning: string;
  created_at: string | null;
  closed_at: string | null;
  evaluation?: ExperimentEvaluation | null;
};

export type ExperimentInput = {
  channel_id?: string | null;
  hypothesis: string;
  variable: string;
  variants: string[];
  metric?: ExperimentMetric;
  project_ids?: string[];
};

export type LessonStatus = 'proposta' | 'aplicada' | 'descartada';
export type LessonOrigin = 'manual' | 'experimento' | 'metricas' | 'retro';
export type Lesson = {
  id: string;
  scope: string;
  text: string;
  evidence: string;
  origin: LessonOrigin | string;
  experiment_id: string | null;
  project_id: string | null;
  status: LessonStatus;
  applied_where: string;
  reason: string;
  created_at: string | null;
  decided_at: string | null;
};
export type LessonInput = {
  scope: string;
  text: string;
  evidence?: string;
  origin?: LessonOrigin;
  experiment_id?: string | null;
  project_id?: string | null;
};

export type ManualMetricInput = Partial<Omit<VideoMetric, 'source' | 'publication_id' | 'project_id' | 'channel_id' | 'updated_at'>> & {
  youtube_id: string;
  /** Canal do vídeo quando ele não tem publicação no Vulto. */
  channel_id?: string | null;
};

const qs = (params: Record<string, string | null | undefined>) => {
  const entries = Object.entries(params).filter((e): e is [string, string] => Boolean(e[1]));
  return entries.length ? `?${new URLSearchParams(entries).toString()}` : '';
};

export const analyticsApi = {
  overview: (channelId?: string | null) => api.get<AnalyticsOverview>(`/api/analytics/overview${qs({ channel_id: channelId })}`),
  video: (youtubeId: string) =>
    api.get<{ video: AnalyticsVideo | null; history: VideoMetric[] }>(`/api/analytics/videos/${encodeURIComponent(youtubeId)}`),
  addMetric: (body: ManualMetricInput) => api.post<VideoMetric>('/api/analytics/metrics', body),
  collect: (channelId?: string | null) => api.post<Job>('/api/analytics/collect', { channel_id: channelId ?? null }),
  variables: () => api.get<{ id: string; label: string }[]>('/api/analytics/variables'),
  experiments: (params: { channelId?: string | null; status?: ExperimentStatus | null } = {}) =>
    api.get<Experiment[]>(`/api/analytics/experiments${qs({ channel_id: params.channelId, status: params.status })}`),
  experiment: (id: string) => api.get<Experiment>(`/api/analytics/experiments/${id}`),
  createExperiment: (body: ExperimentInput) => api.post<Experiment>('/api/analytics/experiments', body),
  updateExperiment: (id: string, body: Partial<Omit<ExperimentInput, 'channel_id'>>) =>
    api.put<Experiment>(`/api/analytics/experiments/${id}`, body),
  /** `save_lesson`: o backend registra o aprendizado como lição no mesmo passo (se falhar, nada fecha). */
  closeExperiment: (id: string, body: { result?: string; learning?: string; abandon?: boolean; save_lesson?: boolean }) =>
    api.post<Experiment>(`/api/analytics/experiments/${id}/close`, body),
  lessons: (params: { scope?: string | null; status?: LessonStatus | null } = {}) =>
    api.get<Lesson[]>(`/api/analytics/lessons${qs({ scope: params.scope, status: params.status })}`),
  createLesson: (body: LessonInput) => api.post<Lesson>('/api/analytics/lessons', body),
  decideLesson: (id: string, body: { status: 'aplicada' | 'descartada'; applied_where?: string; reason?: string }) =>
    api.post<Lesson>(`/api/analytics/lessons/${id}/decide`, body),
};
