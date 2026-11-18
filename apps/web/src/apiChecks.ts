/* Cliente dos relatórios de conferência (ADR 0003): item comum a G e H e a conferência do vídeo final (bloco H). */
import { api, type Job, type Prov } from './api';

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
  kind: 'audio' | 'final' | 'edit';
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

/** Nível (origem) do material na tela, do mais real ao gerado (ADR 0004; `core/sources.py`). */
export type { Prov };

/** Faixa do índice "cara de IA": verde até 30, amarelo até 55, vermelho acima. */
export type AiLookBand = 'verde' | 'amarelo' | 'vermelho';

export type AiLookPart = {
  key: string;
  label: string;
  value: number;
  /** Alvo em texto ("≤ 0.2", "≥ 0.45", "2–5"). */
  target: string;
  /** 0 (no alvo) a 1 (o pior). */
  penalty: number;
  weight: number;
  points: number;
};

/** Métricas da tabela do ADR 0004 §6. */
export type EditFlatMetrics = {
  ai_screen_share: number;
  hook_real: boolean;
  hook_prov: Prov | null;
  real_share: number;
  official_share: number;
  shot_median: number;
  p10: number;
  p90: number;
  shot_cv: number;
  kb_share: number;
  events_per_min: number;
  sfx_per_min: number;
  switch_per_min: number;
  max_same_prov_run: number;
  max_same_prov_run_s: number;
  max_still_s: number;
  repeat_hits: number;
};

/** Trecho seguido com o mesmo nível de fonte (segundos). `scene_id`: a cena onde o trecho começa. */
export type ProvStripSpan = { start: number; end: number; prov: Prov; scene_id: string };

export type EditMetricsSource = 'final' | 'preview' | 'estimate';

/** `GET /projects/{id}/edit-metrics` (ADR 0004 §3.10). */
export type EditMetrics = {
  project_id: string;
  lang: string;
  /** Timeline medida: a do vídeo final, a da última prévia ou a estimada pelo roteiro. */
  source: EditMetricsSource;
  source_label: string;
  version: number | null;
  profile_refs: string[];
  index: { score: number; band: AiLookBand; parts: AiLookPart[] };
  metrics: EditFlatMetrics;
  /** `EditFingerprint` completo (blocos pace/camera/materials/overlays/audio). */
  fingerprint: Record<string, unknown>;
  timeline_fingerprint: Record<string, unknown> | null;
  /** Aderência ao perfil da referência do estilo (null: o estilo não veio de um perfil medido). */
  adherence: {
    score: number | null;
    tolerance: number;
    parts: { field: string; value: number; target: number; score: number }[];
  } | null;
  targets: Record<string, unknown>;
  items: CheckItem[];
  status: CheckLevel;
  strip: ProvStripSpan[];
  duration: number;
  /** Último `QualityReport(kind="edit")` do idioma (auditado no MP4), se houver. */
  report: QualityReport | null;
};

export const checksApi = {
  preflight: (projectId: string, lang: string) =>
    api.get<Preflight>(`/api/projects/${projectId}/preflight?lang=${encodeURIComponent(lang)}`),
  runPreflight: (projectId: string, lang: string) => api.post<Job>(`/api/projects/${projectId}/preflight`, { lang }),
  editMetrics: (projectId: string, lang: string, source: 'auto' | EditMetricsSource = 'auto') =>
    api.get<EditMetrics>(
      `/api/projects/${projectId}/edit-metrics?lang=${encodeURIComponent(lang)}&source=${source}`,
    ),
};
