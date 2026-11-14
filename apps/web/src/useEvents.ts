import { useQueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';
import type { Job } from './api';

/** Assina os eventos do backend (SSE) e atualiza o cache das consultas. */
export const useBackendEvents = () => {
  const client = useQueryClient();
  useEffect(() => {
    const source = new EventSource('/api/events');
    let timer: number | undefined;
    const pending = new Set<string>();
    const flush = () => {
      for (const key of pending) {
        if (key === 'jobs') client.invalidateQueries({ queryKey: ['jobs'] });
        else client.invalidateQueries({ queryKey: ['project', key] });
      }
      client.invalidateQueries({ queryKey: ['projects'] });
      pending.clear();
      timer = undefined;
    };
    const schedule = (key: string) => {
      pending.add(key);
      if (timer === undefined) timer = window.setTimeout(flush, 400);
    };
    source.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as { type: string; job?: Job; project_id?: string };
        if (data.type === 'job' && data.job) {
          client.setQueryData<Job[]>(['jobs'], (old) => {
            if (!old) return old;
            const others = old.filter((j) => j.id !== data.job!.id);
            return [data.job!, ...others].slice(0, 50);
          });
          if (data.job.project_id) {
            client.setQueryData(['job', data.job.id], data.job);
            if (data.job.status !== 'running' || data.job.progress >= 0.999) schedule(data.job.project_id);
            else schedule('jobs');
          }
        } else if (data.type === 'project' && data.project_id) {
          schedule(data.project_id);
        }
      } catch {
        /* evento malformado */
      }
    };
    return () => {
      source.close();
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [client]);
};
