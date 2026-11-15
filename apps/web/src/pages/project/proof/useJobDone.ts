// Acompanha um job pelo id (lista de jobs mantida pelo SSE) e avisa uma vez quando ele termina.
import { useEffect, useRef } from 'react';
import type { Job } from '../../../api';
import { isActiveJob, useJobs } from '../../../shell/data';

/** O job em andamento (ou o último estado dele) e um aviso quando ele sai da fila. */
export const useJobDone = (jobId: string | null, onDone: (job: Job) => void): Job | undefined => {
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
