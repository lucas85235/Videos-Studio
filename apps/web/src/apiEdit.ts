/* Cliente do editor automático (ADR 0004 §3.6): "Reeditar" refaz os subplanos e eventos do projeto pela gramática do
 * estilo, sem LLM e sem gastar cota. As rotas ficam em `backend/studio/api/routes_edit.py`. */
import { api } from './api';

/** 0 calmo (0,5× das cotas), 1 (0,75×), 2 o padrão (1×), 3 agitado (1,4×). */
export type EditIntensity = 0 | 1 | 2 | 3;

export const EDIT_INTENSITY_LABELS: Record<EditIntensity, string> = {
  0: 'Calma',
  1: 'Leve',
  2: 'Normal',
  3: 'Agitada',
};

/** Contagem do que o editor deixou (`edit_plan.summary`). */
export type EditSummary = {
  scenes_with_shots: number;
  shots: number;
  fx: Record<string, number>;
  fx_total: number;
};

export type ReeditResult = {
  /** Versão nova do documento; null quando nada mudou ou o editor está desligado no estilo. */
  version: number | null;
  changed: boolean;
  editor: 'auto' | 'off';
  intensity: EditIntensity;
  summary: EditSummary;
};

export const editApi = {
  /** POST /api/projects/{id}/reedit: refaz a edição (o que é do usuário ou está travado fica). A intensidade escolhida
   * fica gravada no ajuste de estilo do projeto; sem ela, vale a do estilo. */
  reedit: (projectId: string, intensity?: EditIntensity) =>
    api.post<ReeditResult>(`/api/projects/${projectId}/reedit`, intensity === undefined ? {} : { intensity }),
};
