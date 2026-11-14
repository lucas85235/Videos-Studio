// Etapas, próximo passo e filtros da lista de projetos (a partir do status salvo que a API devolve).
// A lista não tem o documento do projeto: a aba inicial fica a cargo da página do projeto (defaultTab).

import { STATUS_LABELS, type Job, type StyleSpec } from '../../api';
import type { BadgeTone } from '../../ui';
import { STEP_LABELS, type StepId } from '../project/core/model';

export type ProjectListItem = {
  id: string;
  title: string;
  style_id: string;
  channel_id: string | null;
  status: string;
  current_version: number;
  updated_at: string | null;
};

/**
 * As cinco etapas de um vídeo, na ordem em que acontecem. Mesma tabela da página do projeto
 * (STEP_LABELS de core/model.ts), para a lista e o projeto nunca darem etapas diferentes.
 */
export const PIPELINE_STEP_IDS = Object.keys(STEP_LABELS) as StepId[];
export const PIPELINE_STEPS = PIPELINE_STEP_IDS.map((id) => STEP_LABELS[id]);

type StatusMeta = {
  /** Nome da etapa no selo (mesmo vocabulário das abas: Roteiro, Cenas, Prévia, Vídeo). */
  label: string;
  tone: BadgeTone;
  /** Índice em PIPELINE_STEPS da etapa em que o vídeo está (a primeira que falta, como em nextStep). */
  step: number;
  /** O que o usuário faz a seguir. */
  next: string;
  /** Um portão de aprovação: o vídeo só anda quando o usuário age. */
  waiting: boolean;
};

const stepIndex = (id: StepId) => PIPELINE_STEP_IDS.indexOf(id);

// A etapa é a primeira ainda não concluída, como em nextStep (core/model.ts):
// "Cenas prontas" quer dizer Roteiro e Cenas concluídos, então a etapa atual é a Prévia.
const STATUS_META: Record<string, StatusMeta> = {
  draft: { label: 'Rascunho', tone: 'neutral', step: stepIndex('script'), next: 'Gerar o roteiro', waiting: false },
  script_review: { label: 'Roteiro para revisar', tone: 'warn', step: stepIndex('script'), next: 'Revisar e aprovar o roteiro', waiting: true },
  storyboard: { label: 'Cenas prontas', tone: 'info', step: stepIndex('preview'), next: 'Conferir as cenas e gerar a prévia', waiting: false },
  preview_ready: { label: 'Prévia para aprovar', tone: 'warn', step: stepIndex('preview'), next: 'Assistir à prévia e aprovar o vídeo', waiting: true },
  rendered: { label: 'Vídeo pronto', tone: 'good', step: stepIndex('publish'), next: 'Vídeo pronto: baixar, publicar ou agendar', waiting: false },
};

const FALLBACK_META: StatusMeta = { label: 'Etapa desconhecida', tone: 'neutral', step: 0, next: 'Abrir o projeto', waiting: false };

export const statusMeta = (status: string): StatusMeta => STATUS_META[status] ?? FALLBACK_META;

/** Em que etapa cada tipo de tarefa trabalha (igual à página do projeto). Sem etapa própria: a etapa atual. */
const JOB_STEP: Record<string, StepId> = {
  script: 'script',
  storyboard: 'scenes',
  translate: 'scenes',
  metadata: 'publish',
  preview: 'preview',
  render: 'video',
  publish_youtube: 'publish',
};

/** Índice da etapa em que uma tarefa falhou (o piloto automático falha na etapa em que o vídeo está). */
export const failedStepOf = (job: Job, status: string): number => {
  const id = JOB_STEP[job.kind];
  return id ? stepIndex(id) : statusMeta(status).step;
};

export const statusLabel = (status: string) => STATUS_META[status]?.label ?? STATUS_LABELS[status] ?? 'Etapa desconhecida';

/** Link para o projeto. Sem ?aba=: a página do projeto escolhe a aba olhando o roteiro, as cenas e os arquivos. */
export const projectLink = (project: ProjectListItem) => `/projetos/${project.id}`;

/* ------------------------------------------------------------------ */
/* Filtros                                                             */
/* ------------------------------------------------------------------ */

export type ProjectFilter = 'todos' | 'esperando' | 'producao' | 'prontos';

export const FILTERS: { id: ProjectFilter; label: string; hint: string }[] = [
  { id: 'todos', label: 'Todos', hint: 'Todos os projetos' },
  {
    id: 'esperando',
    label: 'Esperando você',
    hint: 'Aprovação pendente (pela última etapa salva do projeto) ou tarefa que falhou',
  },
  { id: 'producao', label: 'Em produção', hint: 'Tarefas rodando e vídeos que ainda não chegaram ao fim' },
  { id: 'prontos', label: 'Prontos', hint: 'Vídeo final renderizado' },
];

export const isFilter = (v: string | null): v is ProjectFilter => FILTERS.some((f) => f.id === v);

/**
 * Em que filtro o projeto cai. Com uma tarefa rodando, ele não espera ninguém: está em produção.
 * Uma falha ainda não vista pede ação. Se a última tarefa foi cancelada, a etapa salva pode estar
 * atrasada (ex.: roteiro já aprovado), então o projeto não conta como "esperando aprovação".
 */
export const bucketOf = (
  project: ProjectListItem,
  activeJob: Job | undefined,
  failedJob: Job | undefined,
  canceledJob?: Job,
): Exclude<ProjectFilter, 'todos'> => {
  if (activeJob) return 'producao';
  if (failedJob) return 'esperando';
  if (project.status === 'rendered') return 'prontos';
  if (canceledJob) return 'producao';
  return statusMeta(project.status).waiting ? 'esperando' : 'producao';
};

/** Busca sem acento e sem diferença de maiúsculas. */
export const normalize = (s: string) =>
  s
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .trim();

/* ------------------------------------------------------------------ */
/* Formato                                                             */
/* ------------------------------------------------------------------ */

/** "Curto 9:16" ou "Longo 16:9" (mesmo texto da página do projeto). */
export const formatLabel = (format: StyleSpec['format']) => {
  const aspect = format.aspect || (format.type === 'short' ? '9:16' : '16:9');
  return `${format.type === 'short' ? 'Curto' : 'Longo'} ${aspect}`;
};

/** "~60 s" ou "~8 min". */
export const durationLabel = (seconds: number) => {
  const s = Math.round(seconds);
  if (s < 90) return `~${s} s`;
  return `~${Math.round(s / 60)} min`;
};
