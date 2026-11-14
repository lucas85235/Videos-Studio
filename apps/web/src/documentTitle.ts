import { useEffect, useSyncExternalStore } from 'react';

// Título da página atual (registrado pelo PageHeader) para o título da aba do navegador.
let current: string | null = null;
const listeners = new Set<() => void>();

const set = (value: string | null) => {
  if (value === current) return;
  current = value;
  listeners.forEach((l) => l());
};

const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => listeners.delete(listener);
};

/** Registra o título da página enquanto o componente estiver montado. */
export const usePageTitle = (title: string | null | undefined) => {
  useEffect(() => {
    if (!title) return;
    set(title);
    return () => {
      if (current === title) set(null);
    };
  }, [title]);
};

/** Título registrado pela página atual (ou null). */
export const useCurrentPageTitle = () => useSyncExternalStore(subscribe, () => current);
