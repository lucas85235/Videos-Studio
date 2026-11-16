/* Cliente do bloco C (ADR 0002): operação do canal (Hoje, pendências, rotinas, tempo por etapa, cópia do banco). */
import { api, type Job } from './api';

export type PendingStatus = 'aberta' | 'feita' | 'descartada';

/** Pendência do canal. `where` = onde fazer (link ou caminho no app, ex. "/dinheiro"). */
export type PendingTask = {
  id: string;
  channel_id: string | null;
  text: string;
  where: string;
  kind: 'manual' | 'auto';
  auto_key: string | null;
  status: PendingStatus;
  order: number;
  created_at: string | null;
  done_at: string | null;
};
export type PendingInput = { text: string; where?: string; channel_id?: string | null };
export type PendingPatch = { text?: string; where?: string; channel_id?: string | null; order?: number };

/** Estoque de um canal: prontos (renderizados e não enviados) + agendados no futuro, contra o mínimo. */
export type StockInfo = {
  channel_id: string;
  channel_name: string;
  format: 'short' | 'long' | 'any';
  format_label: string;
  ready: number;
  scheduled: number;
  total: number;
  min_stock: number;
  below: boolean;
};

/** O que só você pode fazer (calculado na hora). `where` = caminho no app. */
export type WaitingKind = 'review' | 'post' | 'youtube' | 'analytics' | 'offer';
export type WaitingItem = { kind: WaitingKind; text: string; where: string; channel_id: string | null; project_id: string | null };

export type Yesterday = {
  date: string;
  published: { project_id: string; title: string; channel_name: string; platform: string; url: string | null; at: string | null }[];
  jobs: number;
  seconds: number;
  failed: { id: string; kind: string; label: string; project_id: string | null; project_title: string | null; error: string }[];
};

export type RoutinesState = {
  /** As rotinas só rodam com os workers ligados (o app normal). */
  enabled: boolean;
  morning_time: string;
  backup: boolean;
  metrics: boolean;
  last_backup: string | null;
  last_morning: string | null;
  last_backup_job: string | null;
  last_morning_job: string | null;
  last_morning_lines: string[];
  /** Última execução do comando `rotinas` (tarefa agendada, com o Vulto fechado). */
  last_offline_run: string | null;
  /** 'travada' só em estados antigos: hoje o "não rodou" fica em `last_offline_skipped`. */
  last_offline_status: 'ok' | 'parcial' | 'travada' | 'falhou' | null;
  last_offline_message: string;
  /** Última vez que a tarefa não rodou porque outro processo (o app aberto) já cuidava das rotinas. */
  last_offline_skipped: string | null;
  last_offline_skipped_message: string;
};

/** Tarefa agendada do Windows que roda as rotinas com o Vulto fechado. */
export type ScheduledTask = {
  name: string;
  /** Só existe no Windows. */
  supported: boolean;
  installed: boolean;
  /** Horário (HH:MM) do disparo diário da tarefa instalada, se deu para ler. */
  installed_time: string | null;
  /** Horário da manhã da página Hoje (o comando de instalar já vai com ele). */
  morning_time: string;
  /** A tarefa instalada dispara em outro horário que não o da manhã: reinstalar. */
  time_mismatch: boolean;
  install_command: string;
  remove_command: string;
  error: string | null;
};
export type RoutinesPatch = { morning_time?: string; backup?: boolean; metrics?: boolean };

/** Tempo por etapa (tipo de job) no período. `pct` de 0 a 1; `per_project` = execuções por projeto. */
export type VelocityStep = {
  kind: string;
  label: string;
  n: number;
  total_s: number;
  avg_s: number;
  pct: number;
  failures: number;
  projects: number;
  per_project: number | null;
  before_s: number;
  cost_usd: number;
};
export type Velocity = {
  days: number;
  since: string;
  until: string;
  jobs: number;
  total_s: number;
  total_prev_s: number;
  failures: number;
  cost_usd: number;
  steps: VelocityStep[];
  rework: { kind: string; label: string; per_project: number; projects: number }[];
  suggestions: string[];
};

export type Today = {
  date: string;
  waiting: WaitingItem[];
  pending: PendingTask[];
  stock: StockInfo[];
  yesterday: Yesterday;
  routines: RoutinesState;
  time: { days: number; total_s: number; steps: VelocityStep[]; suggestions: string[] };
};

export type BackupInfo = { name: string; size: number; created_at: string };

export const opsApi = {
  today: () => api.get<Today>('/api/today'),
  pending: (status: PendingStatus | 'todas' = 'aberta') => api.get<PendingTask[]>(`/api/pending?status=${status}`),
  createPending: (body: PendingInput) => api.post<PendingTask>('/api/pending', body),
  updatePending: (id: string, body: PendingPatch) => api.put<PendingTask>(`/api/pending/${id}`, body),
  setPendingStatus: (id: string, status: PendingStatus) => api.post<PendingTask>(`/api/pending/${id}/status`, { status }),
  routines: () => api.get<RoutinesState>('/api/ops/routines'),
  updateRoutines: (body: RoutinesPatch) => api.put<RoutinesState>('/api/ops/routines', body),
  runMorning: () => api.post<Job>('/api/ops/morning'),
  backupNow: () => api.post<Job>('/api/ops/backup'),
  backups: () => api.get<BackupInfo[]>('/api/ops/backups'),
  scheduledTask: (refresh = false) => api.get<ScheduledTask>(`/api/ops/scheduled-task${refresh ? '?refresh=true' : ''}`),
  velocity: (days = 7) => api.get<Velocity>(`/api/ops/velocity?days=${days}`),
};
