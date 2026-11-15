/* Cliente do bloco D (ADR 0002): banco de ideias e dinheiro (ofertas de afiliado, links na descrição, receita). */
import { api, type ProjectRow } from './api';

export type IdeaStatus = 'nova' | 'aprovada' | 'rejeitada' | 'em_producao' | 'feita' | 'descartada';
export type IdeaFormat = 'short' | 'long' | '';

export const IDEA_STATUS_LABELS: Record<IdeaStatus, string> = {
  nova: 'Nova',
  aprovada: 'Aprovada',
  rejeitada: 'Rejeitada',
  em_producao: 'Em produção',
  feita: 'Feita',
  descartada: 'Descartada',
};

export type Idea = {
  id: string;
  channel_id: string | null;
  title: string;
  angle: string;
  format: IdeaFormat;
  origin: string;
  evidence: string;
  search: string;
  score: number | null;
  status: IdeaStatus;
  reason: string;
  project_id: string | null;
  created_at: string | null;
  decided_at: string | null;
};
export type IdeaInput = {
  title: string;
  channel_id?: string | null;
  angle?: string;
  format?: IdeaFormat;
  origin?: string;
  evidence?: string;
  search?: string;
  score?: number | null;
  status?: IdeaStatus;
  reason?: string;
  /** Cria mesmo com título repetido no canal. */
  force?: boolean;
};

export type FrontStatus = 'ideia' | 'preparando' | 'ativa' | 'bloqueada' | 'pausada' | 'descartada';
export const FRONT_STATUS_LABELS: Record<FrontStatus, string> = {
  ideia: 'Ideia',
  preparando: 'Preparando',
  ativa: 'Ativa',
  bloqueada: 'Bloqueada',
  pausada: 'Pausada',
  descartada: 'Descartada',
};
/** Frente de monetização (como o canal ganha dinheiro além do AdSense). */
export type Front = { id: string; name: string; status: FrontStatus; next: string; requirement: string };

/** Configuração de monetização do canal (`Channel.settings["monetization"]`). */
export type Monetization = {
  plan: string;
  /** 1º mês (AAAA-MM) com link de afiliado no ar. */
  since: string;
  /** ID de rastreamento padrão da Amazon (ex.: plantaovice-20). */
  amazon_tag: string;
  amazon_domain: string;
  max_links: number;
  /** Linha "#publi" do topo da descrição. */
  publi: string;
  /** Texto do comentário fixado (vazio usa o `publi`). */
  publi_pinned: string;
  /** Aviso legal da última linha (só com link da Amazon). */
  disclosure: string;
  /** ID de rastreamento próprio por projeto: {id do projeto: ID}. */
  tracking_ids: Record<string, string>;
  fronts: Front[];
};

export type OfferStatus = 'ideia' | 'ativa' | 'pausada' | 'descartada';
export const OFFER_STATUS_LABELS: Record<OfferStatus, string> = { ideia: 'Ideia', ativa: 'Ativa', pausada: 'Pausada', descartada: 'Descartada' };
export type AffiliateOffer = {
  id: string;
  channel_id: string;
  slug: string;
  name: string;
  store: string;
  asin: string;
  link: string;
  text: string;
  topics: string[];
  status: OfferStatus;
  checked_on: string;
  note: string;
  /** Nunca conferida ou conferida há mais de 3 dias: pede nova conferência de vendedor, estoque e preço. */
  stale: boolean;
};
export type OfferInput = Partial<Omit<AffiliateOffer, 'id' | 'channel_id' | 'stale'>>;

/** Link pronto de uma oferta escolhida para o vídeo. */
export type AffiliateItem = {
  id: string;
  slug: string;
  name: string;
  text: string;
  link: string;
  tracking_id: string;
  store: string;
  checked_on: string;
  note: string;
};

/** Links de afiliado de um projeto (`Project.settings["affiliate"]`) e o que entra na descrição. */
/** Como o projeto escolhe as ofertas: pelos temas ("sempre" + temas) ou só as marcadas. */
export type AffiliateMode = 'temas' | 'ofertas';

export type ProjectAffiliate = {
  enabled: boolean;
  mode: AffiliateMode;
  /** Ids das ofertas (`ofe_...`); projetos antigos podem ter o apelido (slug). */
  offer_ids: string[];
  topics: string[];
  channel_id: string | null;
  is_short: boolean;
  parent_id: string | null;
  parent_title: string | null;
  target: string;
  /** ID de rastreamento próprio deste vídeo (vazio = usa o padrão). */
  tracking_id: string;
  suggested_tracking_id: string;
  /** Todas as ofertas do canal. */
  available: AffiliateOffer[];
  /** Ofertas escolhidas, com o link. */
  offers: AffiliateItem[];
  /** Topo da descrição (linhas "texto: link" + #publi). */
  top: string;
  /** Comentário fixado. */
  pinned: string;
  /** Aviso legal da última linha. */
  end: string;
  checklist: string[];
  errors: string[];
};
export type ProjectAffiliateInput = { enabled?: boolean; mode?: AffiliateMode; offer_ids?: string[]; topics?: string[]; tracking_id?: string };

export type RevenueEntry = {
  id: number;
  channel_id: string;
  store: string;
  period: string;
  tracking_id: string;
  clicks: number | null;
  orders: number | null;
  sales: number | null;
  commission: number | null;
  currency: string;
  note: string;
};
export type RevenueInput = {
  channel_id: string;
  store: string;
  period: string;
  tracking_id?: string;
  clicks?: number | null;
  orders?: number | null;
  sales?: number | null;
  commission?: number | null;
  currency?: string;
  note?: string | null;
};
type RevenueNumbers = { clicks: number | null; orders: number | null; sales: number | null; commission: number | null };
/** Receita de um mês numa loja: soma por ID de rastreamento (sem contar o total da loja de novo). */
export type RevenueMonth = RevenueNumbers & {
  period: string;
  store: string;
  currency: string;
  by_tracking: (RevenueNumbers & { tracking_id: string })[];
};
export type RevenueList = { entries: RevenueEntry[]; summary?: RevenueMonth[] };

export type MoneyOffer = AffiliateOffer & { url: string; tracking_id: string };
export type MoneyChannel = {
  channel_id: string;
  channel_name: string;
  plan: string;
  since: string;
  fronts: Front[];
  offers: MoneyOffer[];
  tracking_ids: Record<string, string>;
  revenue: RevenueMonth[];
};
export type MoneySummary = { channels: MoneyChannel[] };

const qs = (params: Record<string, string | number | null | undefined>) => {
  const entries = Object.entries(params)
    .filter(([, v]) => v !== null && v !== undefined && v !== '')
    .map(([k, v]) => [k, String(v)] as [string, string]);
  return entries.length ? `?${new URLSearchParams(entries).toString()}` : '';
};

export const businessApi = {
  ideas: (params: { channel_id?: string | null; status?: IdeaStatus | null; min_score?: number | null } = {}) =>
    api.get<Idea[]>(`/api/ideas${qs(params)}`),
  createIdea: (body: IdeaInput) => api.post<Idea>('/api/ideas', body),
  updateIdea: (id: string, body: Partial<IdeaInput>) => api.put<Idea>(`/api/ideas/${id}`, body),
  decideIdeas: (body: { ids: string[]; status: IdeaStatus; reason?: string | null }) => api.post<Idea[]>('/api/ideas/decide', body),
  ideaToProject: (id: string, body: { style_id?: string | null; languages?: string[] | null; voice?: { provider: string; voice: string } | null } = {}) =>
    api.post<{ idea: Idea; project: ProjectRow }>(`/api/ideas/${id}/project`, body),
  deleteIdea: (id: string) => api.del<{ deleted: boolean }>(`/api/ideas/${id}`),

  monetization: (channelId: string) => api.get<Monetization>(`/api/channels/${channelId}/monetization`),
  saveMonetization: (channelId: string, body: Partial<Monetization>) => api.put<Monetization>(`/api/channels/${channelId}/monetization`, body),
  offers: (channelId: string) => api.get<AffiliateOffer[]>(`/api/channels/${channelId}/offers`),
  createOffer: (channelId: string, body: OfferInput & { slug: string; name: string }) =>
    api.post<AffiliateOffer>(`/api/channels/${channelId}/offers`, body),
  updateOffer: (channelId: string, offerId: string, body: OfferInput) => api.put<AffiliateOffer>(`/api/channels/${channelId}/offers/${offerId}`, body),
  deleteOffer: (channelId: string, offerId: string) => api.del<{ deleted: boolean }>(`/api/channels/${channelId}/offers/${offerId}`),

  projectAffiliate: (projectId: string) => api.get<ProjectAffiliate>(`/api/projects/${projectId}/affiliate`),
  saveProjectAffiliate: (projectId: string, body: ProjectAffiliateInput) => api.put<ProjectAffiliate>(`/api/projects/${projectId}/affiliate`, body),

  revenue: (params: { channel_id?: string | null; months?: number | null; period?: string | null } = {}) =>
    api.get<RevenueList>(`/api/revenue${qs(params)}`),
  saveRevenue: (body: RevenueInput) => api.post<RevenueEntry>('/api/revenue', body),
  deleteRevenue: (id: number) => api.del<{ deleted: boolean }>(`/api/revenue/${id}`),
  summary: (params: { channel_id?: string | null; months?: number | null } = {}) => api.get<MoneySummary>(`/api/money/summary${qs(params)}`),
};

/** Data de hoje (AAAA-MM-DD) no fuso do navegador. */
export const todayIso = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};
