/* Cliente da importação do histórico do projeto antigo `canais` (só lê a pasta dele). */
import { api } from './api';

/** Destino de um canal do canais: procurar/criar sozinho, ficar de fora ou o id de um canal do Vulto. */
export type CanaisChoice = 'auto' | 'pular' | string;

export type CanaisChannelPlan = {
  slug: string;
  name: string;
  action: 'criar' | 'associar' | 'pular';
  reason: string;
  toml: string;
  youtube_channel_id: string;
  /** Vazio na prévia de um canal que seria criado. */
  vulto_id: string | null;
  vulto_name: string | null;
  /** Rótulos dos campos que seriam preenchidos (só os vazios no Vulto). */
  fields: string[];
  offers_new: number;
  offers_existing: number;
};

export type CanaisTablePlan = {
  key: string;
  label: string;
  total: number;
  new: number;
  existing: number;
  /** Métricas que já existiam e ganham números que estavam vazios. */
  updated: number;
  skipped: number;
  examples: string[];
};

export type CanaisReport = {
  root: string;
  db: string | null;
  dry_run: boolean;
  channels: CanaisChannelPlan[];
  tables: CanaisTablePlan[];
  notes: string[];
};

export type CanaisImportBody = { root: string; channels: Record<string, CanaisChoice> };

export const canaisDefaults = () => api.get<{ default_root: string; exists: boolean }>('/api/import/canais');
export const previewCanais = (body: CanaisImportBody) => api.post<CanaisReport>('/api/import/canais/preview', body);
export const importCanais = (body: CanaisImportBody) => api.post<CanaisReport>('/api/import/canais', body);
