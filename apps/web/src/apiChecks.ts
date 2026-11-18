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
  /** Conferência da edição auditada no MP4 (itens `perfil.*`, no máximo aviso), quando vale para este vídeo. Fica
   * fora do `status`: edição é gosto, não trava o envio. */
  edit?: {
    report_id: number;
    status: CheckLevel;
    score: number | null;
    band: AiLookBand | null;
    items: CheckItem[];
  } | null;
};

/** Nível (origem) do material na tela, do mais real ao gerado (ADR 0004; `core/sources.py`). */
export type { Prov };

/** Faixa do índice "cara de IA": verde até 30, amarelo até 55, vermelho acima. */
export type AiLookBand = 'verde' | 'amarelo' | 'vermelho';

export type AiLookPart = {
  key: string;
  label: string;
  value: number;
  /** Alvo em texto ("≤ 0.2", "≥ 0.45", "2–5"), para o CLI; a tela formata por `lo`, `hi` e `unit`. */
  target: string;
  /** Limite de baixo do alvo (null: sem piso). */
  lo: number | null;
  /** Limite de cima do alvo (null: sem teto). */
  hi: number | null;
  /** share: fração do tempo (em %); ratio: número puro; per_min: por minuto; count: contagem. */
  unit: 'share' | 'ratio' | 'per_min' | 'count';
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

/** Trecho seguido com o mesmo nível de fonte (segundos). `scene_id`: a cena onde o trecho começa; `scene_ids`: todas
 * as cenas do trecho, em ordem (um trecho atravessa cenas). */
export type ProvStripSpan = { start: number; end: number; prov: Prov; scene_id: string; scene_ids: string[] };

export type EditMetricsSource = 'final' | 'preview' | 'estimate';

/** `GET /projects/{id}/edit-metrics` (ADR 0004 §3.10). */
export type EditMetrics = {
  project_id: string;
  lang: string;
  /** Timeline medida: a do vídeo final, a da última prévia ou a estimada pelo roteiro. */
  source: EditMetricsSource;
  source_label: string;
  /** Frase pronta para a tela ("Medido no vídeo final", "Estimado pelo roteiro"; diz quando o projeto mudou). */
  source_text: string;
  /** Versão do projeto que a timeline medida descreve (null: a estimada, que é a de agora). */
  version: number | null;
  current_version: number | null;
  /** A timeline medida é de outra versão ou não bate mais com as cenas: o projeto mudou depois. */
  outdated: boolean;
  /** render: números da conferência auditada no MP4 (vídeo final em dia); timeline: da timeline. */
  measured_from: 'render' | 'timeline';
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
  /** Cenas com algum plano de IA, em ordem e sem repetir (o "Trocar IA por real"). */
  ai_scene_ids: string[];
  duration: number;
  /** Último `QualityReport(kind="edit")` do idioma (auditado no MP4), só quando vale para o vídeo final de agora. */
  report: QualityReport | null;
  /** Havia um relatório da edição, mas de outro render. */
  report_outdated: boolean;
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
