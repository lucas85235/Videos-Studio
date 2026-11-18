/* Cliente do acervo de material real do projeto (ADR 0004 §3.4): `pool.json`, coletado antes do storyboard, e os
 * links oficiais que o usuário dá (trailer, gameplay). As rotas ficam em `backend/studio/api/routes_media.py`; os
 * modelos em `backend/studio/core/project/pool.py`. */
import { api, type Job, type Prov } from './api';

/** Nível (origem) do material na tela, do mais real ao gerado (`Prov` em core/project/document.py). */
export type { Prov };
export type PoolMedia = 'image' | 'clip' | 'capture' | 'meme';

/** Um candidato do acervo (`PoolItem`). Caminhos relativos à pasta de dados (use `fileUrl`). */
export type PoolItem = {
  /** Curto e estável no projeto ("p12"): o que o storyboard cita em `pool_id`. */
  id: string;
  tier: Prov;
  media: PoolMedia;
  desc: string;
  tags: string[];
  entities: string[];
  /** Arquivo local (imagem, captura limpa ou proxy do trecho); null: remoto, baixado no anexo. */
  path: string | null;
  /** Pôster JPEG do trecho. */
  poster: string | null;
  url: string;
  page_url: string;
  start_s: number;
  dur_s: number | null;
  /** Captura: retângulos dos trechos `[x, y, w, h]` (0 a 1) para o destaque animado. */
  rects: number[][];
  w: number;
  h: number;
  license: string;
  credit: string;
  editorial: boolean;
  dhash: string;
  score: number;
  /** Origem com prefixo ("commons:…", "yt:<chave>#3", "acervo:<arquivo>"…). */
  source_id: string;
  /** Tópico do roteiro (o pedido do storyboard agrupa por ele). */
  topic: string;
};

export type Pool = { items: PoolItem[]; gathered_at: string | null; warnings: string[] };

/** Link oficial dado pelo usuário: a coleta baixa e corta em planos. */
export type PoolLink = { url: string; label: string; tier: 'official' | 'own' };

export type PoolSummary = { items: number; by_tier: Partial<Record<Prov, number>>; gathered_at: string | null };

export type PoolState = { pool: Pool; links: PoolLink[]; summary: PoolSummary };

export const PROV_LABELS: Record<Prov, string> = {
  own: 'Próprio',
  official: 'Oficial',
  capture: 'Captura',
  free: 'Foto livre',
  stock: 'Banco',
  graphic: 'Gráfico',
  meme: 'Meme',
  ai: 'IA',
};

export const poolApi = {
  /** GET /api/projects/:id/pool */
  get: (projectId: string) => api.get<PoolState>(`/api/projects/${projectId}/pool`),
  /** POST /api/projects/:id/pool/links — `replace` troca a lista inteira (para tirar um link). */
  setLinks: (projectId: string, links: Array<Partial<PoolLink> & { url: string }>, replace = false) =>
    api.post<{ links: PoolLink[] }>(`/api/projects/${projectId}/pool/links`, { links, replace }),
  /** POST /api/projects/:id/pool/refresh — coleta de novo (job `media_pool`). */
  refresh: (projectId: string) => api.post<Job>(`/api/projects/${projectId}/pool/refresh`),
  /** POST /api/projects/:id/scenes/:sceneId/media (via "pool") — a cena passa a mostrar este item do acervo. */
  attach: (projectId: string, sceneId: string, poolId: string) =>
    api.post<{ version: number; warnings: string[] }>(`/api/projects/${projectId}/scenes/${sceneId}/media`, {
      via: 'pool',
      pool_id: poolId,
    }),
};
