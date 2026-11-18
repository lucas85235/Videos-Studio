/* Cliente do bloco I (ADR 0003): provas na tela (captura com marca-texto), vídeos de referência e miniaturas A/B. */
import { api, type Job, type StyleSpec } from './api';

export type ToolStatus = { ok: boolean; message: string };
export type VisualCapabilities = { browser: ToolStatus; ytdlp: ToolStatus };

/** Trecho marcado: retângulos de 0 a 1 e a janela de enquadramento ([x0, y0, x1, y1]). */
export type CapturePhrase = {
  text: string;
  rects: [number, number, number, number][];
  view: [number, number, number, number] | null;
  lock: boolean;
  /** O trecho foi achado na página (pode estar fora da tela capturada). */
  found?: boolean;
  /** Trecho que manda no recorte da prova. */
  focus?: boolean;
};

export type PageCapture = {
  id: string;
  project_id: string | null;
  scene_id: string | null;
  url: string;
  domain: string;
  title: string;
  /** Caminhos relativos à pasta de dados (use com `fileUrl`). */
  image: string;
  proof: string;
  width: number;
  height: number;
  dark: boolean;
  phrases: CapturePhrase[];
  text_path: string;
  status: 'ok' | 'parcial' | 'falhou';
  error: string | null;
  created_at: string | null;
};

export type ProofInput = { url: string; phrases: string[]; focus?: number };

export type ReferenceShot = { k: number; a: number; b: number; dur: number };

export type ReferenceVideo = {
  id: string;
  title: string;
  source_url: string;
  path: string;
  info: { w?: number; h?: number; fps?: number; codec?: string; dur?: number };
  shots: ReferenceShot[];
  sheets: string[];
  stats: { shots?: number; median_s?: number; mean_s?: number; cuts_per_min?: number; palette?: string[] };
  status: 'novo' | 'pronto' | 'falhou';
  error: string | null;
  created_at: string | null;
  /** Nome do estilo criado a partir dela sem nome digitado: título limpo + " (referência)". */
  suggested_style_name?: string;
};

export type ThumbSpec = {
  scene_id: string | null;
  text: string;
  template: string;
  label?: string;
  /** Com pacote: a miniatura é este candidato (escolhido ou aprovado), desenhado pelo compositor por camadas. */
  candidate_id?: string | null;
  title?: string;
};

/** O pacote que as miniaturas seguem (null sem candidato escolhido). */
export type ThumbsPackage = {
  chosen_id: string;
  locked: boolean;
  candidates: { id: string; title: string; thumb_text: string; template: string; image: string | null; warnings: string[] }[];
};

export type ThumbVariant = ThumbSpec & {
  n: number;
  label: string;
  path: string;
  preview: string;
  size: string;
  mb: number;
  errors: string[];
  /** Avisos que não impedem o uso (ex.: mais de 3 palavras no longo). */
  warnings?: string[];
};

export type ThumbsState = {
  lang: string;
  variants: ThumbVariant[];
  chosen: number;
  spec: ThumbSpec[];
  sheet_big: string;
  sheet_small: string;
  made_at: string | null;
  /** As miniaturas são de antes da última mudança na capa ou nas imagens: o envio usa a capa do vídeo final. */
  stale?: boolean;
  ab: { eligible: boolean; median_impressions: number | null; n: number };
  /** Texto da regra do A/B para mostrar na tela. */
  rule: string;
  /** CTR coletado do vídeo deste projeto no YouTube (null sem publicação). */
  ctr?: {
    youtube_id: string;
    ctr: number | null;
    impressions: number | null;
    age_hours: number | null;
    verdict: 'sem_dado' | 'cedo' | 'ab' | 'trocar' | 'manter';
    swap: number;
    text: string;
  } | null;
  is_long: boolean;
  /** Tamanho final ("3840x2160" no longo, "1080x1920" no Short). */
  size: string;
  templates: string[];
  /** Quantas miniaturas o estilo pede (até 3). */
  max: number;
  /** Com pacote escolhido, as miniaturas são o escolhido e os aprovados (editados na etapa "Título e thumb"). */
  package?: ThumbsPackage | null;
  /** Material oficial liberado nas thumbs deste projeto (canal ou estilo). */
  allow_editorial?: boolean;
};

/** De onde veio cada valor do estilo criado a partir da referência. */
export type RefStyleOrigin = {
  path: string;
  value: unknown;
  source: 'ritmo' | 'paleta' | 'proporcao' | 'texto' | 'preset' | 'llm';
  detail: string;
};

/** POST /api/references/{id}/style: o estilo salvo em Meus estilos e a origem de cada valor. */
export type RefStyleResult = {
  style: StyleSpec;
  version: number;
  base_style_id: string;
  base_reason: string;
  origins: RefStyleOrigin[];
  llm: { used: boolean; provider: string | null; summary: string; message: string };
  rejected: { path: string; error: string }[];
};

/** POST /api/references: a referência criada e o job da análise, que começa sozinho. */
export type ReferenceUpload = ReferenceVideo & { job: Job };

export const THUMB_TEMPLATE_LABELS: Record<string, string> = {
  'texto-grande-esquerda': 'Texto grande à esquerda',
  'texto-centro': 'Texto no centro',
  'texto-baixo': 'Texto embaixo',
  'sem-texto': 'Sem texto',
};

export const visualApi = {
  capabilities: () => api.get<VisualCapabilities>('/api/visual/capabilities'),
  captureProof: (projectId: string, sceneId: string, body: ProofInput) =>
    api.post<Job>(`/api/projects/${projectId}/scenes/${sceneId}/proof`, body),
  captures: (projectId: string) => api.get<PageCapture[]>(`/api/projects/${projectId}/captures`),
  capture: (captureId: string) => api.get<PageCapture>(`/api/captures/${captureId}`),
  setPhraseView: (captureId: string, i: number, view: [number, number, number, number] | null, lock: boolean) =>
    api.put<PageCapture>(`/api/captures/${captureId}/phrases/${i}/view`, { view, lock }),
  references: () => api.get<ReferenceVideo[]>('/api/references'),
  uploadReference: (form: FormData) => api.upload<ReferenceUpload>('/api/references', form),
  referenceFromLink: (url: string, title?: string) => api.post<Job>('/api/references/link', { url, title }),
  reference: (id: string) => api.get<ReferenceVideo>(`/api/references/${id}`),
  renameReference: (id: string, title: string) => api.patch<ReferenceVideo>(`/api/references/${id}`, { title }),
  deleteReference: (id: string) => api.del<{ ok: boolean }>(`/api/references/${id}`),
  analyzeReference: (id: string) => api.post<Job>(`/api/references/${id}/analyze`),
  styleFromReference: (id: string, body: { use_llm: boolean; name?: string }) =>
    api.post<RefStyleResult>(`/api/references/${id}/style`, body),
  thumbs: (projectId: string, lang: string) =>
    api.get<ThumbsState>(`/api/projects/${projectId}/thumbs?lang=${encodeURIComponent(lang)}`),
  setThumbSpec: (projectId: string, lang: string, variants: ThumbSpec[]) =>
    api.put<ThumbsState>(`/api/projects/${projectId}/thumbs/spec`, { lang, variants }),
  makeThumbs: (projectId: string, lang: string) => api.post<Job>(`/api/projects/${projectId}/thumbs`, { lang }),
  chooseThumb: (projectId: string, lang: string, n: number) =>
    api.put<ThumbsState>(`/api/projects/${projectId}/thumbs/chosen`, { lang, n }),
};
