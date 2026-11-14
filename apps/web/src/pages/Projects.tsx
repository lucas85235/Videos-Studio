import { useQuery } from '@tanstack/react-query';
import React from 'react';
import { Link } from 'react-router-dom';
import { api, type Job, JOB_LABELS, STATUS_LABELS } from '../api';
import { Badge, Button, Card, Empty, ProgressBar } from '../ui';

type ProjectListItem = { id: string; title: string; style_id: string; status: string; current_version: number; updated_at: string | null };

export const ProjectsPage: React.FC = () => {
  const projects = useQuery({ queryKey: ['projects'], queryFn: () => api.get<ProjectListItem[]>('/api/projects') });
  const jobs = useQuery({ queryKey: ['jobs'], queryFn: () => api.get<Job[]>('/api/jobs?limit=12'), refetchInterval: 15_000 });
  const active = (jobs.data ?? []).filter((j) => j.status === 'running' || j.status === 'queued');
  return (
    <div className="mx-auto max-w-6xl p-6">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold">Projetos</h1>
          <p className="text-sm text-zinc-500">Cada projeto é um vídeo, em um ou mais idiomas.</p>
        </div>
        <Link to="/novo">
          <Button variant="primary">+ Novo projeto</Button>
        </Link>
      </div>
      {active.length ? (
        <Card className="mb-6">
          <div className="mb-2 text-sm font-semibold">Em andamento</div>
          <div className="flex flex-col gap-3">
            {active.map((job) => (
              <div key={job.id} className="text-sm">
                <div className="mb-1 flex justify-between text-zinc-400">
                  <span>
                    {JOB_LABELS[job.kind] ?? job.kind} · {job.message}
                  </span>
                  <span>{Math.round(job.progress * 100)}%</span>
                </div>
                <ProgressBar value={job.progress} />
              </div>
            ))}
          </div>
        </Card>
      ) : null}
      {projects.data && projects.data.length === 0 ? (
        <Empty>Nenhum projeto ainda. Crie o primeiro em “Novo projeto”.</Empty>
      ) : null}
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
        {(projects.data ?? []).map((p) => (
          <Link key={p.id} to={`/projetos/${p.id}`}>
            <Card className="h-full transition hover:border-zinc-600">
              <div className="mb-2 line-clamp-2 font-semibold text-zinc-100">{p.title}</div>
              <div className="flex flex-wrap items-center gap-1.5 text-xs">
                <Badge tone={p.status === 'rendered' ? 'good' : p.status === 'script_review' ? 'warn' : 'neutral'}>
                  {STATUS_LABELS[p.status] ?? p.status}
                </Badge>
                <Badge>{p.style_id}</Badge>
                <span className="text-zinc-500">v{p.current_version}</span>
              </div>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  );
};
