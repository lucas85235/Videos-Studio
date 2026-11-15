// Acompanha um job do bloco G (curadoria, conferência) pela lista de jobs do SSE e avisa uma vez quando termina.
import { useEffect, useRef } from 'react';
import type { Job } from '../../../../api';
import { isActiveJob, useJobs } from '../../../../shell/data';

export const useAudioJob = (jobId: string | null, onDone: (job: Job) => void): Job | undefined => {
  const jobs = useJobs();
  const job = jobId ? (jobs.data ?? []).find((j) => j.id === jobId) : undefined;
  const done = useRef<string | null>(null);
  const callback = useRef(onDone);
  callback.current = onDone;
  useEffect(() => {
    if (!job || isActiveJob(job) || done.current === job.id) return;
    done.current = job.id;
    callback.current(job);
  }, [job]);
  return job;
};

/** "1:07" a partir de segundos. */
export const clock = (s: number | null | undefined): string => {
  if (s == null || !Number.isFinite(s)) return '–';
  const m = Math.floor(s / 60);
  const r = Math.floor(s - m * 60);
  return `${m}:${String(r).padStart(2, '0')}`;
};

/** "-17,2" (dB com vírgula). */
export const db = (v: number | null | undefined, digits = 1): string =>
  v == null || !Number.isFinite(v) ? '–' : v.toFixed(digits).replace('.', ',');
