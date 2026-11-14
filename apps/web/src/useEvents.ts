import { useQueryClient, type QueryClient } from '@tanstack/react-query';
import { useEffect, useSyncExternalStore } from 'react';
import { JOB_LABELS, type Job, type ProjectDetail } from './api';
import { JOB_DONE_LABELS } from './labels';
import { useToast, type ToastOptions } from './ui';

/* ------------------------------------------------------------------ */
/* Estado da conexão com o servidor local                              */
/* ------------------------------------------------------------------ */

export type ConnectionStatus = 'connecting' | 'online' | 'offline';

let status: ConnectionStatus = 'connecting';
const listeners = new Set<() => void>();

const setStatus = (next: ConnectionStatus) => {
  if (next === status) return;
  status = next;
  listeners.forEach((l) => l());
};

const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => listeners.delete(listener);
};

/** Estado da conexão com o servidor (via SSE de /api/events). */
export const useBackendConnection = () => {
  const value = useSyncExternalStore(subscribe, () => status);
  return { status: value, connected: value === 'online' };
};

/* ------------------------------------------------------------------ */
/* Eventos                                                             */
/* ------------------------------------------------------------------ */

export const JOBS_QUERY_KEY = ['jobs'] as const;
/**
 * Mesmo caminho da página Projetos: duas consultas na chave ['jobs'] com caminhos diferentes fariam o cache
 * alternar de tamanho. Toda leitura de jobs deve passar por useJobs() (shell/data.ts).
 */
export const JOBS_QUERY_PATH = '/api/jobs?limit=12';

const ACTIVE = new Set<Job['status']>(['queued', 'running']);
const TERMINAL = new Set<Job['status']>(['done', 'failed', 'canceled']);

/** Jobs que já geraram aviso de fim (evita aviso duplicado se o estado final chegar duas vezes). */
const toasted = new Set<string>();

type ProjectListItem = { id: string; title: string };

const projectTitle = (client: QueryClient, projectId: string) => {
  const list = client.getQueryData<ProjectListItem[]>(['projects']);
  const fromList = list?.find((p) => p.id === projectId)?.title;
  if (fromList) return fromList;
  return client.getQueryData<ProjectDetail>(['project', projectId])?.doc.title ?? null;
};

const firstLine = (text: string | null | undefined, max = 180) => {
  const line = (text ?? '').split('\n').find((l) => l.trim())?.trim() ?? '';
  return line.length > max ? `${line.slice(0, max - 1)}…` : line;
};

const jobToast = (client: QueryClient, job: Job): ToastOptions | null => {
  const projectId = job.project_id;
  const title = projectId ? projectTitle(client, projectId) : null;
  const projectPath = projectId ? `/projetos/${projectId}` : null;
  const onProject = projectPath !== null && window.location.pathname === projectPath;
  const action = projectPath && !onProject ? { label: 'Abrir projeto', to: projectPath } : undefined;
  const kind = JOB_LABELS[job.kind] ?? 'Tarefa';
  if (job.status === 'done') {
    return { tone: 'good', title: JOB_DONE_LABELS[job.kind] ?? `${kind}: concluído`, description: title ?? undefined, action };
  }
  if (job.status === 'failed') {
    const reason = firstLine(job.error) || firstLine(job.message) || 'O motivo aparece no projeto.';
    return {
      tone: 'bad',
      title: `${kind} falhou`,
      description: title ? `${title}: ${reason}` : reason,
      action,
    };
  }
  return null;
};

/** Assina os eventos do backend (SSE), atualiza o cache das consultas e avisa quando um job termina. */
export const useBackendEvents = () => {
  const client = useQueryClient();
  const { toast } = useToast();

  useEffect(() => {
    let source: EventSource | null = null;
    let disposed = false;
    let lostSince = false;
    let reconnectTimer: number | undefined;
    let offlineTimer: number | undefined;
    let retryMs = 2000;

    // Invalidações agrupadas (evita uma rajada de refetch).
    let flushTimer: number | undefined;
    const pending = new Set<string>();
    const flush = () => {
      for (const key of pending) {
        if (key === '__jobs') client.invalidateQueries({ queryKey: JOBS_QUERY_KEY });
        else if (key === '__projects') client.invalidateQueries({ queryKey: ['projects'] });
        else if (key === '__agenda') client.invalidateQueries({ queryKey: ['agenda'] });
        else client.invalidateQueries({ queryKey: ['project', key] });
      }
      pending.clear();
      flushTimer = undefined;
    };
    const schedule = (...keys: string[]) => {
      keys.forEach((k) => pending.add(k));
      if (flushTimer === undefined) flushTimer = window.setTimeout(flush, 400);
    };

    const onJob = (job: Job) => {
      const list = client.getQueryData<Job[]>(JOBS_QUERY_KEY);
      const previous = list?.find((j) => j.id === job.id) ?? client.getQueryData<Job>(['job', job.id]);
      const statusChanged = previous?.status !== job.status;

      if (list) {
        const index = list.findIndex((j) => j.id === job.id);
        const next = index >= 0 ? list.map((j, i) => (i === index ? job : j)) : [job, ...list].slice(0, 50);
        client.setQueryData<Job[]>(JOBS_QUERY_KEY, next);
      } else {
        schedule('__jobs');
      }
      client.setQueryData(['job', job.id], job);

      if (TERMINAL.has(job.status)) {
        // Estado final: recarrega o projeto mesmo que um refetch já tenha trazido o status (não depende de statusChanged).
        if (job.project_id) schedule(job.project_id);
        schedule('__projects', '__jobs');
        if (job.kind.startsWith('publish')) schedule('__agenda');
        const finishedAt = job.finished_at ? Date.parse(job.finished_at) : Date.now();
        const recent = Number.isNaN(finishedAt) || Date.now() - finishedAt < 2 * 60_000;
        // Avisa se estava ativo, ou se um refetch chegou antes do evento com o mesmo estado final.
        const fromActive = previous ? ACTIVE.has(previous.status) || previous.status === job.status : true;
        if (recent && fromActive && !toasted.has(job.id)) {
          toasted.add(job.id);
          const opts = jobToast(client, job);
          if (opts) toast(opts);
        }
        return;
      }

      if (!statusChanged) return; // tique de progresso: o cache já foi atualizado.
      if (job.project_id) schedule(job.project_id, '__projects');
    };

    const markOnline = () => {
      window.clearTimeout(offlineTimer);
      offlineTimer = undefined;
      retryMs = 2000;
      // Recupera o que mudou (ou falhou) enquanto estava fora, inclusive se o app abriu antes do backend subir.
      if (lostSince) client.invalidateQueries();
      lostSince = false;
      setStatus('online');
    };

    const markLost = () => {
      lostSince = true;
      // Pequena tolerância para quedas instantâneas (o EventSource reconecta sozinho).
      if (offlineTimer === undefined && status !== 'offline') {
        offlineTimer = window.setTimeout(() => {
          offlineTimer = undefined;
          if (lostSince) setStatus('offline');
        }, 1500);
      }
    };

    const connect = () => {
      if (disposed) return;
      source = new EventSource('/api/events');
      source.onopen = markOnline;
      source.onerror = () => {
        markLost();
        // Resposta HTTP de erro (ex.: proxy sem o backend) fecha o EventSource de vez: reabrimos com espera crescente.
        if (source && source.readyState === EventSource.CLOSED) {
          source.close();
          window.clearTimeout(reconnectTimer);
          reconnectTimer = window.setTimeout(connect, retryMs);
          retryMs = Math.min(retryMs * 1.5, 10_000);
        }
      };
      source.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data) as { type: string; job?: Job; project_id?: string };
          if (data.type === 'job' && data.job) onJob(data.job);
          else if (data.type === 'project' && data.project_id) schedule(data.project_id, '__projects');
        } catch {
          /* evento malformado */
        }
      };
    };

    connect();
    return () => {
      disposed = true;
      source?.close();
      window.clearTimeout(reconnectTimer);
      window.clearTimeout(offlineTimer);
      if (flushTimer !== undefined) window.clearTimeout(flushTimer);
    };
  }, [client, toast]);
};
