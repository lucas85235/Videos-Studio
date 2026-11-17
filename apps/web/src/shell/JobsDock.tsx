import {
  Activity,
  CircleX,
  FileText,
  Film,
  Languages,
  LayoutGrid,
  MonitorPlay,
  Tags,
  Telescope,
  Upload,
  Wand2,
  X,
  type LucideIcon,
} from 'lucide-react';
import React, { useEffect, useMemo } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { JOB_LABELS, type Job } from '../api';
import { IconButton, ProgressBar, Spinner } from '../ui';
import { isActiveJob, useJobs, useProjectsIndex, useSeenFailures } from './data';

const JOB_ICONS: Record<string, LucideIcon> = {
  script: FileText,
  storyboard: LayoutGrid,
  translate: Languages,
  metadata: Tags,
  preview: MonitorPlay,
  render: Film,
  autopilot: Wand2,
  publish_youtube: Upload,
  video_research: Telescope,
};

const FAILURE_WINDOW_MS = 24 * 60 * 60 * 1000;

const jobLabel = (job: Job) => JOB_LABELS[job.kind] ?? 'Tarefa';

/** Jobs em andamento e falhas recentes, no rodapé da barra lateral. */
export const JobsDock: React.FC<{ collapsed?: boolean; onNavigate?: () => void }> = ({ collapsed, onNavigate }) => {
  const jobs = useJobs();
  const projects = useProjectsIndex();
  const { seen, markSeen } = useSeenFailures();
  const { pathname } = useLocation();

  const titles = useMemo(() => new Map((projects.data ?? []).map((p) => [p.id, p.title])), [projects.data]);
  const active = useMemo(
    () => (jobs.data ?? []).filter(isActiveJob).sort((a, b) => (a.status === b.status ? 0 : a.status === 'running' ? -1 : 1)),
    [jobs.data],
  );
  const failures = useMemo(
    () =>
      (jobs.data ?? []).filter((j) => {
        if (j.status !== 'failed' || seen.includes(j.id)) return false;
        const at = Date.parse(j.finished_at ?? j.created_at ?? '');
        return Number.isNaN(at) || Date.now() - at < FAILURE_WINDOW_MS;
      }),
    [jobs.data, seen],
  );

  // Abrir o projeto conta como "ver" a falha. Dependência estável (ids), não o array: tiques de progresso não rodam o efeito.
  const openedFailureIds = failures
    .filter((j) => j.project_id && pathname === `/projetos/${j.project_id}`)
    .map((j) => j.id)
    .join(',');
  useEffect(() => {
    if (openedFailureIds) markSeen(...openedFailureIds.split(','));
  }, [openedFailureIds, markSeen]);

  if (!active.length && !failures.length) return null;

  const titleOf = (job: Job) => (job.project_id ? (titles.get(job.project_id) ?? 'Projeto') : 'Geral');
  const linkOf = (job: Job) => {
    if (job.project_id) return `/projetos/${job.project_id}`;
    // A pesquisa de vídeos é por canal, sem projeto: leva para a tela da pesquisa daquele canal.
    if (job.kind === 'video_research' && typeof job.payload?.channel_id === 'string') return `/pesquisa?canal=${encodeURIComponent(job.payload.channel_id)}`;
    return '/';
  };

  if (collapsed) {
    const first = active[0] ?? failures[0];
    const summary = [
      active.length ? `${active.length} ${active.length === 1 ? 'tarefa em andamento' : 'tarefas em andamento'}` : null,
      failures.length ? `${failures.length} ${failures.length === 1 ? 'falha' : 'falhas'}` : null,
    ]
      .filter(Boolean)
      .join(', ');
    return (
      <Link
        to={linkOf(first)}
        onClick={onNavigate}
        title={summary}
        aria-label={summary}
        className={`relative mx-auto flex h-9 w-9 items-center justify-center rounded-lg border ${
          failures.length && !active.length ? 'border-red-400/40 bg-red-400/10 text-red-300' : 'border-line bg-card text-accent'
        }`}
      >
        {active.length ? <Spinner className="h-4 w-4" /> : <CircleX size={16} aria-hidden="true" />}
        <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-raised px-1 text-xs font-semibold leading-none text-fg tabular-nums ring-1 ring-line-strong">
          {active.length + failures.length}
        </span>
      </Link>
    );
  }

  return (
    <section aria-label="Tarefas em andamento" className="flex flex-col gap-1.5">
      {active.length ? (
        <h2 className="px-1 font-sans text-xs font-semibold uppercase tracking-[0.08em] text-subtle">Em andamento</h2>
      ) : null}
      <ul className="flex flex-col gap-1.5">
        {active.slice(0, 4).map((job) => {
          const Icon = JOB_ICONS[job.kind] ?? Activity;
          const pct = Math.round(job.progress * 100);
          const queued = job.status === 'queued';
          return (
            <li key={job.id}>
              <Link
                to={linkOf(job)}
                onClick={onNavigate}
                className="flex flex-col gap-1.5 rounded-lg border border-line bg-card p-2 transition-colors hover:border-line-strong hover:bg-raised"
              >
                <span className="flex items-center gap-2 text-[13px]">
                  <Icon size={14} strokeWidth={2} aria-hidden="true" className="shrink-0 text-accent" />
                  <span className="min-w-0 flex-1 truncate font-medium text-fg">{jobLabel(job)}</span>
                  <span className="shrink-0 text-xs text-muted tabular-nums">{queued ? 'na fila' : `${pct}%`}</span>
                </span>
                <span className="truncate text-xs text-subtle" title={job.message || undefined}>
                  {titleOf(job)}
                  {job.message ? ` · ${job.message}` : ''}
                </span>
                <ProgressBar
                  value={job.progress}
                  indeterminate={queued}
                  valueText={queued ? 'Na fila' : undefined}
                  label={`${jobLabel(job)}: ${titleOf(job)}`}
                />
              </Link>
            </li>
          );
        })}
        {active.length > 4 ? <li className="px-1 text-xs text-subtle">e mais {active.length - 4}…</li> : null}
        {failures.slice(0, 3).map((job) => (
          <li key={job.id} className="flex items-start gap-1 rounded-lg border border-red-400/35 bg-red-400/10 p-2">
            <Link to={linkOf(job)} onClick={() => { markSeen(job.id); onNavigate?.(); }} className="flex min-w-0 flex-1 items-start gap-2 rounded-sm">
              <CircleX size={14} strokeWidth={2} aria-hidden="true" className="mt-0.5 shrink-0 text-red-300" />
              <span className="flex min-w-0 flex-col">
                <span className="text-[13px] font-medium text-red-200">{jobLabel(job)} falhou</span>
                <span className="truncate text-xs text-muted">{titleOf(job)}</span>
              </span>
            </Link>
            <IconButton label={`Dispensar aviso: ${jobLabel(job)} falhou em ${titleOf(job)}`} icon={X} size="sm" onClick={() => markSeen(job.id)} className="-my-1 -mr-1" />
          </li>
        ))}
      </ul>
    </section>
  );
};
