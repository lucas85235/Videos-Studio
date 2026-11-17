/* Cliente da pesquisa de vídeos do canal: sinais (próprio canal, concorrentes, buscas, comentários), síntese com a web,
 * checagem contra o que já foi feito e ideias com data de publicação (rotas /api/research). */
import { api, type Job } from './api';
import type { IdeaStatus } from './apiBusiness';
import type { RadarAccess } from './apiRadar';

export type ResearchMarket = 'br' | 'us';
export type ResearchFormat = 'long' | 'short';
export type ResearchStep = 'own' | 'competitors' | 'keywords' | 'comments' | 'synthesis';
/** ok = a síntese rodou; parcial = a síntese não rodou (só os sinais); cancelada; falhou = erro depois da coleta. */
export type ResearchStatus = 'ok' | 'parcial' | 'cancelada' | 'falhou';

export const RESEARCH_MARKET_LABELS: Record<ResearchMarket, string> = { br: 'Brasil (português)', us: 'EUA (inglês)' };
export const RESEARCH_MARKET_SHORT: Record<ResearchMarket, string> = { br: 'BR', us: 'EUA' };
export const RESEARCH_FORMAT_LABELS: Record<ResearchFormat, string> = { long: 'Vídeo longo', short: 'Short' };
export const RESEARCH_STEP_LABELS: Record<ResearchStep, string> = {
  own: 'Próprio canal',
  competitors: 'Concorrentes',
  keywords: 'Buscas',
  comments: 'Comentários',
  synthesis: 'Síntese',
};
export const RESEARCH_STATUS_LABELS: Record<ResearchStatus, string> = { ok: 'Completa', parcial: 'Parcial', cancelada: 'Cancelada', falhou: 'Falhou' };

/** Limites da config (iguais aos do backend: `config.py`). */
export const RESEARCH_LIMITS = { keywords: 8, competitors: 20, budget: [100, 3000], days: [14, 365] } as const;

export type ResearchCompetitor = { id: string; name: string; handle: string };

export type ResearchConfig = {
  competitors: ResearchCompetitor[];
  /** Termos de busca (vazio salvo = as 5 primeiras palavras-chave do canal no YouTube; ver `keywords_default`). */
  keywords: string[];
  /** Termos em inglês para o mercado "us" (vazio = os mesmos `keywords`). */
  keywords_us: string[];
  markets: ResearchMarket[];
  /** Formatos escolhidos à mão (vazio = deduzidos dos estilos do canal). */
  formats: ResearchFormat[];
  budget: number;
  days: number;
  auto: boolean;
  keywords_default: boolean;
  last_week: string | null;
  last_week_submitted: string | null;
  last_week_tries: number | null;
  last_run_id: string | null;
  last_job: string | null;
};

export type ResearchConfigInput = Partial<Pick<ResearchConfig, 'competitors' | 'keywords' | 'keywords_us' | 'markets' | 'formats' | 'budget' | 'days' | 'auto'>>;

/** A conta da cota de uma rodada, sem rede: unidades da cota geral (com o teto `budget`) e buscas, que têm balde
 * próprio no YouTube (`search_limit` por dia, somando o Radar; `search_left` = quantas cabem hoje, se o medidor sabe). */
export type ResearchPlan = {
  competitors: number;
  competitors_units: number;
  keyword_units: number;
  total: number;
  budget: number;
  cabe: boolean;
  searches: { term: string; market: ResearchMarket }[];
  search_calls: number;
  search_left: number | null;
  search_limit: number;
  pulados: string[];
  pulados_motivo: '' | 'orçamento' | 'buscas do dia';
};

export type ResearchRunSummary = {
  id: string;
  channel_id: string;
  job_id: string | null;
  created_at: string | null;
  finished_at: string | null;
  status: ResearchStatus;
  error: string | null;
  units: number;
  n_ideas: number;
  n_saved: number;
  /** O que não deu para medir ou verificar nesta rodada. */
  lacunas: string[];
  params: {
    markets?: ResearchMarket[];
    days?: number;
    budget?: number;
    competitors?: string[];
    keywords?: string[];
    keywords_us?: string[];
    formats?: ResearchFormat[];
    skip?: ResearchStep[];
    weekly?: string | null;
    search_calls?: number;
  };
};

export type ResearchState = {
  channel_id: string;
  config: ResearchConfig;
  formats: ResearchFormat[];
  access: RadarAccess;
  web: { ok: boolean; message: string };
  plan: ResearchPlan;
  last_run: ResearchRunSummary | null;
  /** Quando a próxima rodada manual fica liberada (6 h entre rodadas), ou null se já pode. */
  next_manual_at: string | null;
  active_job: Job | null;
};

/* ------------------------------------------------------------------ sinais */

export type ResearchOwnVideo = {
  youtube_id: string;
  title: string;
  format: ResearchFormat;
  views: number | null;
  vpd: number;
  mult: number | null;
  ctr: number | null;
  pct_watched: number | null;
  published_at: string | null;
  /** Vídeo novo (ainda não assentado): fora da mediana, mult provisório. */
  provisorio?: boolean;
  url: string;
};
export type ResearchOwnFormat = {
  /** Vídeos públicos com views (a base medida). */
  n: number;
  /** Vídeos públicos do formato no canal (sem login do canal: os que o Vulto conhece). */
  n_publicos?: number;
  n_assentados?: number;
  /** Menos de 3 assentados no formato: a mediana é a do canal todo (ou a dos novos). */
  base_fraca?: boolean;
  base?: 'formato' | 'canal' | 'novos';
  med: number;
  top: ResearchOwnVideo[];
  bottom: ResearchOwnVideo[];
};
export type ResearchOwnSuggestion = { kind: string; text: string };

export type ResearchCompetitorVideo = {
  id: string;
  title: string;
  channel_id: string;
  channel: string;
  views: number;
  dur: number | null;
  format: ResearchFormat;
  idade_d: number;
  published_at: string;
  url: string;
  /** Mediana do próprio concorrente no formato (vídeos com 7+ dias). */
  med: number;
  mult: number;
};
export type ResearchCompetitorChannel = {
  id: string;
  name: string;
  handle: string;
  subs: number | null;
  n: number;
  med_long: number;
  med_short: number;
  url: string;
};

export type ResearchKeywordVideo = {
  id: string;
  title: string;
  channel: string;
  channel_id: string;
  views: number;
  subs: number | null;
  /** Views ÷ inscritos (mínimo de 1.000). */
  ratio: number;
  term: string;
  market: ResearchMarket;
  format: ResearchFormat;
  dur: number | null;
  idade_d: number;
  outlier: boolean;
  url: string;
};

export type ResearchComment = {
  id: string;
  video_id: string | null;
  video_title: string;
  text: string;
  likes: number;
  kind: 'pergunta' | 'pedido';
  url: string;
};

export type ResearchSignals = {
  own?: { long?: ResearchOwnFormat; short?: ResearchOwnFormat; suggestions?: ResearchOwnSuggestion[] };
  competitors?: { items: ResearchCompetitorVideo[]; channels: ResearchCompetitorChannel[] };
  keywords?: { items: ResearchKeywordVideo[]; searches: { term: string; market: ResearchMarket; n: number }[]; pulados: string[] };
  comments?: { items: ResearchComment[] };
  falhas?: { etapa: string; erro: string }[];
};

/* ------------------------------------------------------------------ síntese */

export type ResearchEvidenceKind = 'proprio' | 'concorrente' | 'busca' | 'comentario' | 'web';
export const RESEARCH_EVIDENCE_LABELS: Record<ResearchEvidenceKind, string> = {
  proprio: 'Meu canal',
  concorrente: 'Concorrente',
  busca: 'Busca',
  comentario: 'Comentário',
  web: 'Web',
};

export type ResearchEvidence = {
  kind: ResearchEvidenceKind;
  /** P#, C#, K# ou M# (vazio na evidência "web"). */
  ref: string;
  url: string;
  title: string;
  note: string;
  checked?: boolean;
  term?: string;
};

export type ResearchWhenType = 'evergreen' | 'data' | 'janela';
export const RESEARCH_WHEN_LABELS: Record<ResearchWhenType, string> = { evergreen: 'Qualquer dia', data: 'Data certa', janela: 'Janela' };
export type ResearchWhen = { type: ResearchWhenType; date: string; until: string; note: string };

export type ResearchCheckStatus = 'nova' | 'parecida' | 'repetida' | 'ja_no_banco';
export type ResearchCheck = {
  status: ResearchCheckStatus;
  similar_to: { title: string; url: string } | null;
  similarity?: number;
  idea_id: string | null;
  saved?: boolean;
  /** Situação atual da ideia gravada no banco (null se não está no banco). */
  idea_status?: IdeaStatus | null;
};

export type ResearchRisk = 'baixo' | 'medio' | 'alto';
export const RESEARCH_RISK_LABELS: Record<ResearchRisk, string> = { baixo: 'Risco baixo', medio: 'Risco médio', alto: 'Risco alto' };

export type ResearchIdea = {
  rank: number;
  title: string;
  format: ResearchFormat;
  angle: string;
  why: string;
  evidence: ResearchEvidence[];
  when: ResearchWhen;
  needs: string[];
  score: number;
  risk: { level: ResearchRisk; note: string };
  no_proof?: boolean;
  check?: ResearchCheck;
};

export type ResearchAgendaItem = {
  date: string;
  until: string;
  type: ResearchWhenType;
  rank: number;
  title: string;
  format: ResearchFormat;
  score: number;
};

export type ResearchSynthesis = {
  resumo?: string;
  temas?: string[];
  ideas?: ResearchIdea[];
  web_sources?: { title: string; url: string; date: string }[];
  discarded?: { title: string; motivo: string }[];
  agenda?: ResearchAgendaItem[];
};

export type ResearchRun = ResearchRunSummary & {
  llm_tokens: number;
  llm_api_equiv_usd: number;
  signals: ResearchSignals;
  synthesis: ResearchSynthesis;
  idea_ids: string[];
};

export type ResearchSaveResult = {
  created: string[];
  skipped: { rank: number; motivo: string; idea_id: string | null }[];
  run: ResearchRun;
};

const q = encodeURIComponent;

/** Endereço do relatório em Markdown para baixar (arquivo .md). */
export const researchReportDownloadUrl = (runId: string) => `/api/research/runs/${q(runId)}/report?download=true`;

export const researchApi = {
  state: (channelId: string) => api.get<ResearchState>(`/api/research?channel_id=${q(channelId)}`),
  saveConfig: (channelId: string, changes: ResearchConfigInput) =>
    api.put<ResearchConfig>('/api/research/config', { channel_id: channelId, ...changes }),
  resolveCompetitor: (link: string) =>
    api.post<ResearchCompetitor & { url: string; subs: number | null }>('/api/research/competitors/resolve', { link }),
  simulate: (channelId: string, skip: ResearchStep[] = []) => api.post<ResearchPlan>('/api/research/simulate', { channel_id: channelId, skip }),
  run: (channelId: string, skip: ResearchStep[] = []) => api.post<Job>('/api/research/run', { channel_id: channelId, skip }),
  runs: (channelId: string, limit = 20) => api.get<ResearchRunSummary[]>(`/api/research/runs?channel_id=${q(channelId)}&limit=${limit}`),
  getRun: (runId: string) => api.get<ResearchRun>(`/api/research/runs/${q(runId)}`),
  report: (runId: string) => api.get<{ markdown: string; filename: string }>(`/api/research/runs/${q(runId)}/report`),
  saveIdeas: (runId: string, ranks: number[], force = false) =>
    api.post<ResearchSaveResult>(`/api/research/runs/${q(runId)}/ideas`, { ranks, force }),
};
