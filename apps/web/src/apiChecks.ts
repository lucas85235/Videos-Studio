/* Cliente dos relatórios de conferência (ADR 0003): item comum a G e H e a conferência do vídeo final (bloco H). */
import { api, type Job } from './api';

export type CheckLevel = 'ok' | 'aviso' | 'erro';

/** Item de conferência (igual na conferência de áudio, na do vídeo final e nos avisos rápidos). */
export type CheckItem = {
  code: string;
  level: CheckLevel;
  title: string;
  detail: string;
  at_s: number | null;
  scene_id: string | null;
  /** Erro que nem "publicar mesmo assim" libera (ex.: voz ou imagens só de prévia). */
  blocking?: boolean;
};

export type QualityReport = {
  id: number;
  project_id: string;
  kind: 'audio' | 'final';
  lang: string;
  version: number;
  source: string;
  status: CheckLevel;
  items: CheckItem[];
  data: Record<string, unknown>;
  /** Folha de contato relativa à pasta de dados (use com `fileUrl`). */
  contact_sheet: string;
  created_at: string | null;
};

/** Medidas do vídeo final em `QualityReport.data`. */
export type FinalCheckData = {
  lufs?: number | null;
  lra?: number | null;
  true_peak?: number | null;
  duration?: number | null;
  pauses?: { at_s: number; voice_db: number | null; music_db: number | null; diff_db: number | null }[];
  black?: { start: number; end: number; dur: number }[];
};

export type Preflight = {
  lang: string;
  /** Vídeo final do idioma (relativo), se existir. */
  video: string | null;
  report: QualityReport | null;
  /** O relatório é do mesmo vídeo e da mesma versão. */
  fresh: boolean;
  quick: CheckItem[];
  status: CheckLevel;
  /** Tudo o que conta para o estado: os avisos rápidos de agora e, com relatório fresco, as medidas dele. */
  items: CheckItem[];
};

export const checksApi = {
  preflight: (projectId: string, lang: string) =>
    api.get<Preflight>(`/api/projects/${projectId}/preflight?lang=${encodeURIComponent(lang)}`),
  runPreflight: (projectId: string, lang: string) => api.post<Job>(`/api/projects/${projectId}/preflight`, { lang }),
};
