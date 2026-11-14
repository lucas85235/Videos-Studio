import { useQuery } from '@tanstack/react-query';
import { useSyncExternalStore } from 'react';
import { api, type Agenda, type Job, type Provider } from '../api';
import { JOBS_QUERY_KEY, JOBS_QUERY_PATH, useBackendConnection } from '../useEvents';

export type ProjectIndexItem = { id: string; title: string; status: string };

export const isActiveJob = (job: Job) => job.status === 'queued' || job.status === 'running';

/** Jobs recentes (mesma chave da página Projetos: o SSE mantém este cache vivo). */
export const useJobs = () => {
  const { connected } = useBackendConnection();
  return useQuery({
    queryKey: JOBS_QUERY_KEY,
    queryFn: () => api.get<Job[]>(JOBS_QUERY_PATH),
    // Com o SSE no ar, o cache é atualizado pelos eventos; o polling é só um plano B.
    refetchInterval: connected ? 60_000 : 15_000,
  });
};

/** Lista de projetos (para mostrar o título do projeto de cada job). */
export const useProjectsIndex = () => useQuery({ queryKey: ['projects'], queryFn: () => api.get<ProjectIndexItem[]>('/api/projects') });

/** Agenda (mesma chave da página Agenda), para o contador de pacotes manuais pendentes. */
export const useAgenda = () => useQuery({ queryKey: ['agenda'], queryFn: () => api.get<Agenda>('/api/agenda'), refetchInterval: 60_000 });

/** Provedores (mesma chave da página Configurações). */
export const useProviders = () => useQuery({ queryKey: ['providers'], queryFn: () => api.get<Provider[]>('/api/providers'), staleTime: 60_000 });

export type Readiness = {
  /** Provedor real (uso comercial) disponível, ou null quando só há prévia. */
  real: Provider | null;
  quotaExhausted: boolean;
};

export const readinessOf = (providers: Provider[] | undefined, kind: string): Readiness => {
  const list = (providers ?? []).filter((p) => p.kind === kind);
  const real = list.find((p) => p.available && p.commercial_use) ?? null;
  return { real, quotaExhausted: Boolean(real?.quota_exhausted_today) };
};

/* Preferências locais (localStorage pode falhar em janelas privadas ou vir editado/corrompido). */

export const isBoolean = (v: unknown): v is boolean => typeof v === 'boolean';
export const isStringArray = (v: unknown): v is string[] => Array.isArray(v) && v.every((x) => typeof x === 'string');

/** Lê um JSON do localStorage; devolve `fallback` se faltar, não for JSON ou não passar no validador. */
export const readLocal = <T,>(key: string, fallback: T, isValid: (v: unknown) => v is T): T => {
  try {
    const raw = window.localStorage.getItem(key);
    if (raw == null) return fallback;
    const value: unknown = JSON.parse(raw);
    return isValid(value) ? value : fallback;
  } catch {
    return fallback;
  }
};

export const writeLocal = (key: string, value: unknown) => {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* sem armazenamento: a preferência vale só nesta sessão */
  }
};

const SEEN_KEY = 'vulto.falhasVistas';

// Store de módulo: todas as instâncias (barra desktop oculta e gaveta) compartilham a mesma lista.
let seenStore: string[] = typeof window === 'undefined' ? [] : readLocal<string[]>(SEEN_KEY, [], isStringArray);
const seenListeners = new Set<() => void>();
const subscribeSeen = (listener: () => void) => {
  seenListeners.add(listener);
  return () => {
    seenListeners.delete(listener);
  };
};
const getSeen = () => seenStore;

const markSeen = (...ids: string[]) => {
  const fresh = ids.filter((id) => !seenStore.includes(id));
  if (!fresh.length) return;
  seenStore = [...seenStore, ...fresh].slice(-100);
  writeLocal(SEEN_KEY, seenStore);
  seenListeners.forEach((l) => l());
};

/** Falhas de job que o usuário já viu (somem do destaque da barra lateral). */
export const useSeenFailures = () => {
  const seen = useSyncExternalStore(subscribeSeen, getSeen, getSeen);
  return { seen, markSeen };
};
