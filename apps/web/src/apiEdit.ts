/* Cliente do editor automático (ADR 0004 §3.6): "Reeditar" refaz os subplanos e eventos do projeto pela gramática do
 * estilo, sem LLM e sem gastar cota. As rotas ficam em `backend/studio/api/routes_edit.py`. */
import { api, type BeatMark, type FxEvent, type Prov, type SubShot } from './api';

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

/** Palavra falada da cena: o gatilho (`at`) que a acha e o instante estimado (s desde o início da cena). */
export type TrackWord = { text: string; at: string; t: number };
export type TrackShot = SubShot & { i: number; t: number; prov: Prov; desc: string };
export type TrackFx = FxEvent & { i: number; t: number };

/** Uma cena na trilha de edição (`GET /projects/{id}/edit-track`). */
export type TrackScene = {
  scene_id: string;
  order: number;
  start_s: number;
  dur_s: number;
  prov: Prov;
  locked: boolean;
  kind: string;
  mark: BeatMark;
  words: TrackWord[];
  shots: TrackShot[];
  fx: TrackFx[];
};

export type EditTrack = {
  editor: 'auto' | 'off';
  intensity: EditIntensity;
  summary: EditSummary;
  scenes: TrackScene[];
};

/** O evento como o servidor guarda (sem os campos da trilha). */
export const plainFx = ({ type, at, dur_ms, params, origin, locked }: FxEvent): FxEvent => ({ type, at, dur_ms, params, origin, locked });
export const plainShot = (shot: SubShot & Partial<Pick<TrackShot, 'i' | 't' | 'prov' | 'desc'>>): SubShot => {
  const { i: _i, t: _t, prov: _p, desc: _d, ...rest } = shot;
  return rest;
};

export const editApi = {
  /** GET /api/projects/{id}/edit-track: por cena, palavras com gatilho, subplanos e eventos. */
  track: (projectId: string) => api.get<EditTrack>(`/api/projects/${projectId}/edit-track`),
  /** PATCH .../scenes/{sid}/fx: a lista inteira de eventos da cena (travar, apagar, mover gatilho). */
  setFx: (projectId: string, sceneId: string, fx: FxEvent[]) =>
    api.patch<{ version: number | null; changed: boolean }>(`/api/projects/${projectId}/scenes/${sceneId}/fx`, { fx: fx.map(plainFx) }),
  /** PATCH .../scenes/{sid}/shots: a lista inteira de subplanos da cena. */
  setShots: (projectId: string, sceneId: string, shots: SubShot[]) =>
    api.patch<{ version: number | null; changed: boolean }>(`/api/projects/${projectId}/scenes/${sceneId}/shots`, {
      shots: shots.map(plainShot),
    }),
  /** POST /api/projects/{id}/reedit: refaz a edição (o que é do usuário ou está travado fica). A intensidade escolhida
   * fica gravada no ajuste de estilo do projeto; sem ela, vale a do estilo. */
  reedit: (projectId: string, intensity?: EditIntensity) =>
    api.post<ReeditResult>(`/api/projects/${projectId}/reedit`, intensity === undefined ? {} : { intensity }),
};
