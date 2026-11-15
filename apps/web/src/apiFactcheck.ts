/* Cliente do bloco F (ADR 0003): checagem de fatos no roteiro. */
import { api, type Job } from './api';

export type FactVerdict = 'ok' | 'corrigir' | 'sem_fonte' | 'boato';
export type FactClaimStatus = 'aberta' | 'aplicada' | 'ignorada';

export type FactClaim = {
  id: string;
  project_id: string;
  run_id: string | null;
  segment_id: string;
  scene_id: string | null;
  lang: string;
  segment_hash: string;
  excerpt: string;
  claim: string;
  verdict: FactVerdict;
  source_title: string;
  url: string;
  quote: string;
  /** null = não conferido. */
  quote_found: boolean | null;
  check_note: string;
  correction: string;
  note: string;
  status: FactClaimStatus;
  /** O texto do segmento mudou (ou o segmento foi apagado) desde a checagem. */
  stale?: boolean;
  checked_at: string | null;
  created_at: string | null;
  updated_at: string | null;
};

export type FactCheckSummary = {
  ok: number;
  corrigir: number;
  sem_fonte: number;
  boato: number;
  open: number;
  stale: number;
  quote_missing: number;
  unchecked: number;
  /** Ids dos trechos sem checagem (novos ou editados depois da última). */
  unchecked_ids: string[];
  /** Correções já aplicadas no roteiro. */
  applied: number;
  ignored: number;
  /** Trechos do roteiro com texto neste idioma. */
  segments: number;
  /** Afirmações abertas (corrigir, sem fonte, boato) por segmento. */
  open_by_segment: Record<string, number>;
};

export type FactCheckState = {
  claims: FactClaim[];
  summary: FactCheckSummary;
  lang: string;
  web: { available: boolean; reason: string };
  last_job: string | null;
  /** Quando a última checagem deste idioma terminou (ISO). */
  checked_at: string | null;
  /** A última checagem usou busca web? */
  last_web: boolean | null;
  /** Job de checagem em andamento (ou o mais recente). */
  job: Job | null;
};

export type FactApplyResult = { claim: FactClaim; version: number };

export type FactCheckRunInput = { lang?: string; segment_ids?: string[] };

export type FactClaimPatch = {
  verdict?: FactVerdict;
  source_title?: string;
  url?: string;
  quote?: string;
  correction?: string;
  note?: string;
  status?: 'aberta' | 'ignorada';
};

const base = (projectId: string) => `/api/projects/${projectId}/factcheck`;

export const factcheckApi = {
  get: (projectId: string, lang: string) => api.get<FactCheckState>(`${base(projectId)}?lang=${encodeURIComponent(lang)}`),
  run: (projectId: string, body: FactCheckRunInput = {}) => api.post<Job>(base(projectId), body),
  update: (projectId: string, claimId: string, body: FactClaimPatch) => api.patch<FactClaim>(`${base(projectId)}/${claimId}`, body),
  verify: (projectId: string, claimId: string) => api.post<FactClaim>(`${base(projectId)}/${claimId}/verify`),
  apply: (projectId: string, claimId: string) => api.post<FactApplyResult>(`${base(projectId)}/${claimId}/apply`),
  /** Tabela fala → fonte em Markdown (texto, não JSON): abra ou baixe pelo endereço. */
  sourcesUrl: (projectId: string, lang: string) => `${base(projectId)}/sources.md?lang=${encodeURIComponent(lang)}`,
};
