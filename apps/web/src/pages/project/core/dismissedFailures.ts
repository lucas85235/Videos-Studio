// Falhas que o usuário dispensou na página do projeto (botão Dispensar da faixa "Próximo passo").
// Separado de 'vulto.falhasVistas' (shell/data.ts): aquela lista só apaga o aviso da barra lateral e é
// marcada assim que o projeto abre; esta só muda quando o usuário dispensa a falha aqui.

import { useSyncExternalStore } from 'react';
import { isStringArray, readLocal, writeLocal } from '../../../shell/data';

const KEY = 'vulto.falhasDispensadas';

let store: string[] = typeof window === 'undefined' ? [] : readLocal<string[]>(KEY, [], isStringArray);
const listeners = new Set<() => void>();

const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
};
const get = () => store;

const dismiss = (id: string) => {
  if (store.includes(id)) return;
  store = [...store, id].slice(-100);
  writeLocal(KEY, store);
  listeners.forEach((l) => l());
};

/** Falhas dispensadas na página do projeto (ficam dispensadas entre visitas). */
export const useDismissedFailures = () => {
  const dismissed = useSyncExternalStore(subscribe, get, get);
  return { dismissed, dismiss };
};
