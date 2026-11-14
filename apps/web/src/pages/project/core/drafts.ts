import { useCallback, useEffect, useRef, useSyncExternalStore } from 'react';
import type { ProjectDoc } from '../../../api';

/**
 * Rascunhos guardados fora dos componentes (por projeto): sobrevivem à troca de aba, de idioma, de página
 * e à chegada de uma versão nova do servidor. Só saem quando o usuário salva ou descarta.
 */
export type Draft = {
  /** Texto digitado. */
  value: string;
  /** Texto do servidor quando o rascunho começou (para saber se o servidor mudou por baixo). */
  base: string;
};

type Bucket<T> = Record<string, T>;

const EMPTY: Bucket<never> = Object.freeze({}) as Bucket<never>;

export type KeyedStore<T> = {
  get: (projectId: string) => Bucket<T>;
  set: (projectId: string, key: string, value: T | null) => void;
  update: (projectId: string, fn: (current: Bucket<T>) => Bucket<T>) => void;
  subscribe: (listener: () => void) => () => void;
  /** Algum projeto tem alguma entrada? */
  hasAny: () => boolean;
};

export const createKeyedStore = <T,>(): KeyedStore<T> => {
  let state: Record<string, Bucket<T>> = {};
  const listeners = new Set<() => void>();
  const emit = () => listeners.forEach((l) => l());
  const get = (projectId: string) => state[projectId] ?? (EMPTY as Bucket<T>);
  const update = (projectId: string, fn: (current: Bucket<T>) => Bucket<T>) => {
    const current = get(projectId);
    const next = fn(current);
    if (next === current) return;
    state = { ...state, [projectId]: next };
    emit();
  };
  return {
    get,
    update,
    set: (projectId, key, value) =>
      update(projectId, (current) => {
        if (value === null) {
          if (!(key in current)) return current;
          const { [key]: _removed, ...rest } = current;
          return rest;
        }
        return { ...current, [key]: value };
      }),
    subscribe: (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    hasAny: () => Object.values(state).some((bucket) => Object.keys(bucket).length > 0),
  };
};

export const useKeyedStore = <T,>(store: KeyedStore<T>, projectId: string) => {
  const getSnapshot = useCallback(() => store.get(projectId), [store, projectId]);
  return useSyncExternalStore(store.subscribe, getSnapshot, getSnapshot);
};

/** Uma única entrada do store: o componente só re-renderiza quando ESTA chave muda. */
export const useKeyedValue = <T,>(store: KeyedStore<T>, projectId: string, key: string): T | undefined => {
  const getSnapshot = useCallback(() => store.get(projectId)[key], [store, projectId, key]);
  return useSyncExternalStore(store.subscribe, getSnapshot, getSnapshot);
};

/** Rascunhos do roteiro: chave `${idioma}|${segmentoId}`. */
export const scriptDrafts = createKeyedStore<Draft>();
export const scriptDraftKey = (lang: string, segmentId: string) => `${lang}|${segmentId}`;
export const parseScriptDraftKey = (key: string) => {
  const i = key.indexOf('|');
  return { lang: key.slice(0, i), segmentId: key.slice(i + 1) };
};

/** Rascunho de roteiro cujo trecho (ou idioma) não existe mais no documento atual. */
export const isOrphanScriptDraft = (doc: ProjectDoc, key: string) => {
  const { lang, segmentId } = parseScriptDraftKey(key);
  return !doc.languages.includes(lang) || !doc.segments.some((s) => s.id === segmentId);
};

/** Rascunhos dos prompts de imagem das cenas: chave = id da cena. */
export const promptDrafts = createKeyedStore<Draft>();

/** Campos soltos do projeto ainda não salvos: chaves 'topic', 'brief' e 'title'. */
export type FieldKey = 'topic' | 'brief' | 'title';
export const fieldDrafts = createKeyedStore<Draft>();

/** Cenas alteradas nesta sessão: chave = id da cena, valor = quando (ms). */
export const sceneChanges = createKeyedStore<number>();

/** Aviso "versão restaurada" (chave 'last'): sobrevive à troca de aba até a prévia nova ou o fechar. */
export type RestoreNotice = { from: number; to: number | null; at: number };
export const restoreNotices = createKeyedStore<RestoreNotice>();

/** Quantos rascunhos o projeto tem (roteiro, prompts e campos soltos). */
export const useUnsavedCount = (projectId: string) => {
  const script = useKeyedStore(scriptDrafts, projectId);
  const prompts = useKeyedStore(promptDrafts, projectId);
  const fields = useKeyedStore(fieldDrafts, projectId);
  return { script: Object.keys(script).length, prompts: Object.keys(prompts).length, fields: Object.keys(fields).length };
};

/**
 * Poda dos rascunhos quando chega uma versão nova do documento (roda no ProjectPage, sempre montado):
 * - roteiro: sai o rascunho que ficou igual ao texto salvo; o de trecho que sumiu FICA (a aba Roteiro lista para copiar ou descartar);
 * - prompts: sai o que ficou igual ao salvo e o de cena que não existe mais (avisado por `onPrunedPrompts`);
 * - campos soltos: sai o que ficou igual ao salvo.
 */
export const useDraftReconcile = (doc: ProjectDoc | undefined, onPrunedPrompts: (count: number) => void) => {
  const notify = useRef(onPrunedPrompts);
  notify.current = onPrunedPrompts;
  useEffect(() => {
    if (!doc) return;
    scriptDrafts.update(doc.id, (current) => {
      let next: Bucket<Draft> | null = null;
      for (const [key, d] of Object.entries(current)) {
        const { lang, segmentId } = parseScriptDraftKey(key);
        const seg = doc.segments.find((s) => s.id === segmentId);
        if (seg && (seg.text[lang] ?? '') === d.value) {
          next ??= { ...current };
          delete next[key];
        }
      }
      return next ?? current;
    });
    let orphans = 0;
    promptDrafts.update(doc.id, (current) => {
      let next: Bucket<Draft> | null = null;
      for (const [sceneId, d] of Object.entries(current)) {
        const scene = doc.scenes.find((s) => s.id === sceneId);
        if (!scene || scene.visual.prompt === d.value) {
          if (!scene) orphans += 1;
          next ??= { ...current };
          delete next[sceneId];
        }
      }
      return next ?? current;
    });
    fieldDrafts.update(doc.id, (current) => {
      let next: Bucket<Draft> | null = null;
      for (const [key, d] of Object.entries(current)) {
        const server = key === 'topic' ? doc.topic : key === 'brief' ? doc.brief : key === 'title' ? doc.title : null;
        if (server === d.value) {
          next ??= { ...current };
          delete next[key];
        }
      }
      return next ?? current;
    });
    if (orphans) notify.current(orphans);
  }, [doc]);
};

// Um único aviso do navegador para todos os projetos: rascunhos sobrevivem à navegação dentro do app,
// então quem fecha a janela em outra página também precisa ser avisado.
if (typeof window !== 'undefined') {
  window.addEventListener('beforeunload', (event) => {
    if (![scriptDrafts, promptDrafts, fieldDrafts].some((store) => store.hasAny())) return;
    event.preventDefault();
    // Navegadores antigos exigem returnValue para mostrar o aviso.
    event.returnValue = '';
  });
}
