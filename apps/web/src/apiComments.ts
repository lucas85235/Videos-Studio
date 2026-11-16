/* Cliente da caixa de comentários do YouTube. Rotas em /api/comments/*. */
import { api } from './api';

export type CommentFilter = 'todos' | 'nao_respondidos' | 'perguntas' | 'nao_lidos';

/** Fio de comentários de um vídeo do canal. `read`, sugestão e resposta pelo app são só do Vulto. */
export type YouTubeComment = {
  id: string;
  channel_id: string;
  video_id: string;
  video_title: string;
  video_url: string;
  comment_url: string;
  author: string;
  text: string;
  like_count: number;
  reply_count: number;
  can_reply: boolean;
  is_question: boolean;
  answered: boolean;
  read: boolean;
  suggestion: string;
  suggestion_provider: string;
  reply_text: string;
  replied_at: string | null;
  published_at: string | null;
};

/** Situação da caixa por canal ligado ao YouTube. */
export type InboxState = {
  channel_id: string;
  name: string;
  state: 'ok' | 'sem_escopo' | 'sem_token' | 'publico' | 'erro' | 'nunca';
  error: string;
  last_run: string | null;
};

/** Cota do YouTube gasta hoje (unidades). A cota é do projeto do Google Cloud: todos os canais dividem. */
export type CommentsQuota = { comments: number; youtube: number; daily: number; list_cost: number; reply_cost: number };

export type CommentsPage = {
  items: YouTubeComment[];
  total: number;
  counts: Record<CommentFilter, number>;
  videos: { video_id: string; title: string; count: number }[];
  channels: InboxState[];
  quota: CommentsQuota;
};

export type RefreshResult = {
  channels: (InboxState & { new: number; updated: number; units: number })[];
  new: number;
  units: number;
  quota: CommentsQuota;
};

const query = (params: Record<string, string | null | undefined>) => {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v) q.set(k, v);
  const text = q.toString();
  return text ? `?${text}` : '';
};

export const commentsApi = {
  list: (opts: { channelId?: string | null; filter: CommentFilter; videoId?: string | null; limit?: number }) =>
    api.get<CommentsPage>(
      `/api/comments${query({ channel_id: opts.channelId, filter: opts.filter, video_id: opts.videoId, limit: opts.limit ? String(opts.limit) : null })}`,
    ),
  refresh: (channelId: string | null) => api.post<RefreshResult>('/api/comments/refresh', { channel_id: channelId }),
  markRead: (ids: string[], read = true) => api.post<{ changed: number }>('/api/comments/read', { ids, read }),
  /** Marca como lidos os comentários do canal, filtro e vídeo escolhidos (o que está na tela). */
  markAllRead: (channelId: string | null, filter: CommentFilter, videoId: string | null) =>
    api.post<{ changed: number }>('/api/comments/read-all', { channel_id: channelId, filter, video_id: videoId }),
  suggest: (id: string) => api.post<YouTubeComment>(`/api/comments/${encodeURIComponent(id)}/suggest`),
  /** Envia a resposta ao YouTube. Só chamar depois da confirmação do usuário. */
  reply: (id: string, text: string) =>
    api.post<{ comment: YouTubeComment; quota: CommentsQuota }>(`/api/comments/${encodeURIComponent(id)}/reply`, { text, confirm: true }),
};
