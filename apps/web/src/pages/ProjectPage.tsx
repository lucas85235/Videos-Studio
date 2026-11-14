import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import React, { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api, type Job, JOB_LABELS, type ProjectDetail, STATUS_LABELS } from '../api';
import { Badge, Button, ErrorBox, ProgressBar, Tabs } from '../ui';
import { OutputTab } from './project/OutputTab';
import { PreviewTab } from './project/PreviewTab';
import { ScenesTab } from './project/ScenesTab';
import { ScriptTab } from './project/ScriptTab';
import { VersionsTab } from './project/VersionsTab';

export const useProject = (projectId: string) =>
  useQuery({ queryKey: ['project', projectId], queryFn: () => api.get<ProjectDetail>(`/api/projects/${projectId}`) });

export const useStartJob = (projectId: string) => {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ kind, payload }: { kind: string; payload?: Record<string, unknown> }) =>
      api.post<Job>(`/api/projects/${projectId}/jobs`, { kind, payload: payload ?? {} }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['project', projectId] }),
  });
};

const JobRow: React.FC<{ job: Job }> = ({ job }) => {
  const client = useQueryClient();
  const cancel = useMutation({
    mutationFn: () => api.post(`/api/jobs/${job.id}/cancel`),
    onSuccess: () => client.invalidateQueries({ queryKey: ['project', job.project_id] }),
  });
  const live = useQuery({ queryKey: ['job', job.id], queryFn: () => api.get<Job>(`/api/jobs/${job.id}`), initialData: job, refetchInterval: 5_000 });
  const j = live.data ?? job;
  return (
    <div className="flex items-center gap-3 text-sm">
      <div className="w-32 shrink-0 text-zinc-300">{JOB_LABELS[j.kind] ?? j.kind}</div>
      <div className="min-w-0 flex-1">
        <div className="mb-1 truncate text-xs text-zinc-500">{j.message}</div>
        <ProgressBar value={j.progress} />
      </div>
      <div className="w-10 text-right text-xs text-zinc-400">{Math.round(j.progress * 100)}%</div>
      <Button variant="ghost" onClick={() => cancel.mutate()} disabled={cancel.isPending}>
        Cancelar
      </Button>
    </div>
  );
};

export const ProjectPage: React.FC = () => {
  const { projectId = '' } = useParams();
  const navigate = useNavigate();
  const project = useProject(projectId);
  const startJob = useStartJob(projectId);
  const client = useQueryClient();
  const [tab, setTab] = useState('script');
  const gates = useMutation({
    mutationFn: (patch: Record<string, boolean>) => api.patch(`/api/projects/${projectId}/settings`, { gates: patch }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['project', projectId] }),
  });
  const remove = useMutation({
    mutationFn: () => api.del(`/api/projects/${projectId}`),
    onSuccess: () => navigate('/'),
  });

  if (project.isLoading) return <div className="p-6 text-zinc-500">Carregando…</div>;
  if (project.error || !project.data) return <div className="p-6"><ErrorBox error={project.error ?? 'Projeto não encontrado'} /></div>;
  const { doc, project: row, warnings } = project.data;
  const active = project.data.jobs.filter((j) => j.status === 'running' || j.status === 'queued');
  const failed = project.data.jobs.find((j) => j.status === 'failed' && j.finished_at && Date.now() - Date.parse(j.finished_at) < 10 * 60_000);
  const busy = active.length > 0;
  const g = row.settings.gates ?? {};

  return (
    <div className="mx-auto max-w-7xl p-6">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="mb-1 text-xs text-zinc-500">
            <Link to="/" className="hover:text-zinc-300">Projetos</Link> / {row.id}
          </div>
          <h1 className="text-xl font-bold">{doc.title}</h1>
          <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs">
            <Badge tone={row.status === 'rendered' ? 'good' : row.status === 'script_review' ? 'warn' : 'neutral'}>
              {STATUS_LABELS[row.status] ?? row.status}
            </Badge>
            <Link to={`/estilos/${doc.style_id}`}><Badge tone="info">estilo: {doc.style_id}</Badge></Link>
            <Badge>{doc.languages.join(' · ')}</Badge>
            <Badge>v{row.current_version}</Badge>
            <span className="text-zinc-500">{doc.segments.length} segmentos · {doc.scenes.length} cenas</span>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button busy={startJob.isPending} disabled={busy} onClick={() => startJob.mutate({ kind: 'autopilot', payload: {} })}>
            ▶ Piloto automático
          </Button>
          <Button variant="primary" disabled={busy || !doc.scenes.length} onClick={() => startJob.mutate({ kind: 'preview' })}>
            Gerar prévia
          </Button>
          <Button disabled={busy || !doc.scenes.length} onClick={() => startJob.mutate({ kind: 'render' })}>
            Renderizar vídeo
          </Button>
          <Button variant="ghost" onClick={() => window.confirm('Apagar este projeto? Os arquivos gerados continuam no cache.') && remove.mutate()}>
            Apagar
          </Button>
        </div>
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-4 text-xs text-zinc-400">
        <span>Portões de aprovação:</span>
        {(['script', 'preview'] as const).map((gate) => (
          <label key={gate} className="flex items-center gap-1.5">
            <input type="checkbox" checked={g[gate] ?? true} onChange={(e) => gates.mutate({ [gate]: e.target.checked })} />
            {gate === 'script' ? 'revisar roteiro' : 'revisar prévia'}
          </label>
        ))}
      </div>

      {warnings.length ? (
        <div className="mb-4 rounded-lg border border-amber-800/60 bg-amber-950/30 p-3 text-xs text-amber-200">
          {warnings.map((w) => (
            <div key={w}>• {w}</div>
          ))}
        </div>
      ) : null}
      {active.length ? (
        <div className="mb-4 flex flex-col gap-2 rounded-xl border border-zinc-800 bg-zinc-900/60 p-3">
          {active.map((job) => (
            <JobRow key={job.id} job={job} />
          ))}
        </div>
      ) : null}
      {failed ? (
        <div className="mb-4">
          <ErrorBox error={`${JOB_LABELS[failed.kind] ?? failed.kind} falhou: ${failed.error}`} />
        </div>
      ) : null}
      <ErrorBox error={startJob.error} />

      <Tabs
        tabs={[
          { id: 'script', label: '1. Roteiro' },
          { id: 'scenes', label: '2. Cenas' },
          { id: 'preview', label: '3. Prévia e edição' },
          { id: 'output', label: '4. Vídeo e publicação' },
          { id: 'versions', label: 'Versões' },
        ]}
        active={tab}
        onChange={setTab}
      />
      <div className="pt-4">
        {tab === 'script' ? <ScriptTab detail={project.data} busy={busy} /> : null}
        {tab === 'scenes' ? <ScenesTab detail={project.data} busy={busy} /> : null}
        {tab === 'preview' ? <PreviewTab detail={project.data} busy={busy} /> : null}
        {tab === 'output' ? <OutputTab detail={project.data} busy={busy} /> : null}
        {tab === 'versions' ? <VersionsTab detail={project.data} /> : null}
      </div>
    </div>
  );
};
