/* Cliente do pacote "título e thumb primeiro" (seção A.6 da especificação): candidatos (título + thumb), escolha,
 * aprovação e as referências da pesquisa para a prévia do feed. As rotas ficam em `backend/studio/api/routes_package.py`.
 * As respostas são lidas de forma tolerante: um servidor sem a rota (ou com campos a menos) não quebra a tela. */
import {
  api,
  type Channel,
  type Job,
  type PackageCandidate,
  type PackageDoc,
  type StyleSpec,
  type ThumbLayers,
} from './api';

/* ------------------------------------------------------------------ tipos */

/** Vídeo de referência (outlier da pesquisa/radar) para comparar no feed. */
export type PackageReference = {
  id: string;
  title: string;
  channel: string;
  views: number | null;
  mult: number | null;
  /** Miniatura do YouTube (i.ytimg.com/vi/{id}/hqdefault.jpg). */
  thumb_url: string;
  url: string;
  /** Duração em segundos (para o selo do feed), quando conhecida. */
  dur: number | null;
};

/** Pose do avatar que o servidor libera para a thumb deste projeto (canal + projeto + estilo já aplicados). */
export type PackagePose = { id: string; emotions: string[]; aliases: string[]; facing: string };

/** Resposta normalizada de GET /api/projects/{id}/package. */
export type PackageState = {
  package: PackageDoc;
  references: PackageReference[];
  /** Material oficial (trailer, capa, logo) liberado nas thumbs deste projeto. null: o servidor não disse. */
  allow_editorial: boolean | null;
  /** Texto do aviso de direitos (servidor); a tela tem um padrão. */
  editorial_notice: string | null;
  /** Avisos do servidor (ex.: "o gancho foi escrito antes do pacote"). */
  warnings: string[];
  /** Templates oferecidos (estilo; sem avatar, sem `avatar-reage`; Short: só `livre`). null: o servidor não disse. */
  templates: string[] | null;
  title_candidates: number | null;
  thumb_candidates: number | null;
  /** Biblioteca de thumbs do canal que este projeto pode usar (o material oficial some quando o canal não libera). */
  assets: ThumbkitItem[];
  /** Avatar na thumb pelo servidor: null quando o servidor não disse (a tela calcula pelo canal). */
  presenter: { enabled: boolean; poses: PackagePose[] } | null;
};

/** Item da biblioteca de thumbs do canal (`library/thumbkit/<canal>/thumbkit.json`). */
export type ThumbkitItem = {
  id: string;
  kind: 'frame' | 'cutout' | 'logo' | 'object' | string;
  /** Arquivo relativo à pasta da biblioteca do canal (`library/thumbkit/<canal>/`). */
  file: string | null;
  /** Endereço da imagem para a tela (`/files/...`), ou null. */
  src: string | null;
  tags: string[];
  credit: string;
  editorial: boolean;
  focus_x?: number | null;
  /** Trailer de origem (frames tirados de vídeo: `source.video`/`source.t`) e duração, para o seletor de tempo. */
  video?: string | null;
  t?: number | null;
  duration_s?: number | null;
};

/** `append`: soma os novos candidatos aos que já existem ("Gerar mais"). */
export type GeneratePackageInput = { n_titles?: number; n_thumbs?: number; keep_manual?: boolean; append?: boolean };
export type UploadedThumbkitItem = ThumbkitItem & { path: string | null };
export type CandidatePatch = Partial<Pick<PackageCandidate, 'title' | 'thumb_text' | 'promise' | 'concept' | 'layers'>>;

/* ------------------------------------------------------------------ constantes */

export const PACKAGE_TITLE_MAX = 90;
export const THUMB_WORDS_MAX = 3;
export const PACKAGE_STATUS = 'package_review';
/** Jobs do pacote (mesmos nomes do backend). */
export const PACKAGE_JOB_KINDS = new Set(['package', 'package_thumbs']);

export const DEFAULT_EDITORIAL_NOTICE =
  'Thumb com material oficial (quadros de trailer, capa, logos): o risco de reivindicação de direitos fica com o canal. Personagens oficiais nunca são gerados por IA.';

export const TEMPLATE_LABELS: Record<string, string> = {
  'heroi-palavra': 'Herói + palavra',
  'avatar-reage': 'Avatar reage',
  'lugar-mapa': 'Lugar / mapa',
  'numero-lista': 'Número + lista',
  versus: 'Versus',
  livre: 'Livre (Short)',
  prova: 'Prova (print + marcação)',
  objeto: 'Objeto em close',
};

export const EMPTY_PACKAGE: PackageDoc = { candidates: [], chosen_id: null, locked: false, references: [], generated_at: null };

/* ------------------------------------------------------------------ normalização */

type Loose = Record<string, unknown>;
const isObj = (v: unknown): v is Loose => Boolean(v) && typeof v === 'object' && !Array.isArray(v);
const str = (v: unknown): string => (typeof v === 'string' ? v : '');
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []);

/** Miniatura pública de um vídeo do YouTube (não gasta cota da API). */
export const ytThumb = (videoId: string) => `https://i.ytimg.com/vi/${encodeURIComponent(videoId)}/hqdefault.jpg`;

/** Referência a partir de um id solto ou de um objeto da pesquisa/radar (campos variam entre as fontes). */
export const normalizeReference = (raw: unknown): PackageReference | null => {
  if (typeof raw === 'string') {
    const id = raw.trim();
    return id ? { id, title: '', channel: '', views: null, mult: null, thumb_url: ytThumb(id), url: `https://www.youtube.com/watch?v=${id}`, dur: null } : null;
  }
  if (!isObj(raw)) return null;
  const id = str(raw.id) || str(raw.youtube_id) || str(raw.video_id);
  if (!id) return null;
  return {
    id,
    title: str(raw.title),
    channel: str(raw.channel) || str(raw.channel_title),
    views: num(raw.views),
    mult: num(raw.mult),
    thumb_url: str(raw.thumbnail_url) || str(raw.thumb_url) || str(raw.thumbnail) || ytThumb(id),
    url: str(raw.url) || `https://www.youtube.com/watch?v=${id}`,
    dur: num(raw.dur) ?? num(raw.duration_s),
  };
};

const normalizeDoc = (raw: unknown, fallback: PackageDoc): PackageDoc => {
  const src = isObj(raw) && isObj(raw.package) ? raw.package : raw;
  if (!isObj(src) || !Array.isArray(src.candidates)) return fallback;
  return {
    candidates: src.candidates as PackageCandidate[],
    chosen_id: typeof src.chosen_id === 'string' ? src.chosen_id : null,
    locked: Boolean(src.locked),
    references: strings(src.references),
    generated_at: typeof src.generated_at === 'string' ? src.generated_at : null,
  };
};

/**
 * Lê a resposta do GET do pacote. Aceita `PackageDoc` puro, `{package: PackageDoc}` ou o `PackageDoc` com as
 * referências resolvidas em `reference_videos`/`references` (objetos). Ids soltos viram miniaturas do YouTube.
 */
export const normalizePackageState = (raw: unknown, fallback: PackageDoc = EMPTY_PACKAGE, channelId: string | null = null): PackageState => {
  const obj = isObj(raw) ? raw : {};
  const pkg = normalizeDoc(raw, fallback);
  const resolved = [obj.references_resolved, obj.reference_videos, obj.references].find((v) => Array.isArray(v) && v.some(isObj));
  const refs = (Array.isArray(resolved) ? resolved : pkg.references).map(normalizeReference).filter((r): r is PackageReference => r !== null);
  const seen = new Set<string>();
  const assets = isObj(obj.assets) ? obj.assets : null;
  const presenter = isObj(obj.presenter) ? obj.presenter : null;
  return {
    package: pkg,
    references: refs.filter((r) => {
      if (seen.has(r.id)) return false;
      seen.add(r.id);
      return true;
    }),
    allow_editorial: typeof obj.allow_editorial === 'boolean' ? obj.allow_editorial : null,
    editorial_notice: str(obj.editorial_notice) || null,
    warnings: [...strings(obj.warnings), ...(str(obj.notice) ? [str(obj.notice)] : [])],
    templates: Array.isArray(obj.templates) ? strings(obj.templates) : null,
    title_candidates: num(obj.title_candidates),
    thumb_candidates: num(obj.thumb_candidates),
    assets: assets
      ? normalizeThumbkit([...asList(assets.frames), ...asList(assets.cutouts), ...asList(assets.logos), ...asList(assets.objects)], channelId)
      : [],
    presenter: presenter
      ? {
          enabled: Boolean(presenter.enabled),
          poses: asList(presenter.poses)
            .filter(isObj)
            .map((p) => ({ id: str(p.id), emotions: strings(p.emotions), aliases: strings(p.aliases), facing: str(p.facing) || 'center' }))
            .filter((p) => p.id),
        }
      : null,
  };
};

const asList = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

/** Endereço de um arquivo da biblioteca de thumbs do canal (o `file` do manifesto é relativo à pasta do canal). */
export const thumbkitSrc = (channelId: string | null, file: string | null) =>
  file && channelId ? `/files/library/thumbkit/${encodeURIComponent(channelId)}/${file.replace(/^\/+/, '')}` : null;

/** Itens do manifesto da biblioteca (aceita a lista pura ou `{items}`). */
export const normalizeThumbkit = (raw: unknown, channelId: string | null = null): ThumbkitItem[] => {
  const list = Array.isArray(raw) ? raw : isObj(raw) && Array.isArray(raw.items) ? raw.items : [];
  return list.filter(isObj).flatMap((it) => {
    const id = str(it.id);
    if (!id) return [];
    const source = isObj(it.source) ? it.source : {};
    const file = str(it.file) || null;
    return [
      {
        id,
        kind: str(it.kind) || 'frame',
        file,
        src: str(it.url) || thumbkitSrc(channelId, file),
        tags: strings(it.tags),
        credit: str(it.credit),
        editorial: Boolean(it.editorial),
        focus_x: num(it.focus_x),
        video: str(source.video) || str(it.video) || null,
        t: num(source.t) ?? num(it.t),
        duration_s: num(it.duration_s) ?? num(source.duration_s),
      },
    ];
  });
};

/* ------------------------------------------------------------------ regras puras (espelho do backend) */

/** Palavras do texto da thumb. */
export const wordCount = (text: string) => text.split(/\s+/).filter(Boolean).length;

const fold = (s: string) =>
  s
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, ' ');

/** Palavras do texto da thumb que já estão no título (a checagem `repeat_title` aceita no máximo 1). */
export const repeatedWords = (title: string, thumbText: string): string[] => {
  const inTitle = new Set(fold(title).split(/\s+/).filter((w) => w.length > 2));
  return Array.from(new Set(fold(thumbText).split(/\s+/).filter((w) => w.length > 2 && inTitle.has(w))));
};

/** Avisos calculados na tela (antes de o servidor checar a imagem): palavras demais, título longo, repetição. */
export const localWarnings = (c: Pick<PackageCandidate, 'title' | 'thumb_text'>, isShort: boolean): string[] => {
  const out: string[] = [];
  const words = wordCount(c.thumb_text);
  if (!isShort && words > THUMB_WORDS_MAX) out.push(`Texto da thumb com ${words} palavras (máximo ${THUMB_WORDS_MAX}; ideal 1 a 2).`);
  if (c.title.length > PACKAGE_TITLE_MAX) out.push(`Título com ${c.title.length} caracteres (máximo ${PACKAGE_TITLE_MAX}).`);
  const rep = repeatedWords(c.title, c.thumb_text);
  if (rep.length > 1) out.push(`A thumb repete ${rep.length} palavras do título (${rep.join(', ')}): ela deve somar, não repetir.`);
  return out;
};

/** Todos os avisos de um candidato: os do servidor (`score.warnings`) e os locais, sem repetir. */
export const candidateWarnings = (c: PackageCandidate, isShort: boolean): string[] =>
  Array.from(new Set([...strings(c.score?.warnings), ...localWarnings(c, isShort)]));

/** Hash curto (FNV-1a) para o cache da imagem da prévia: muda quando as camadas ou o título mudam. */
export const layersHash = (c: Pick<PackageCandidate, 'layers' | 'title' | 'image'>) => {
  const text = `${c.image ?? ''}|${c.title}|${JSON.stringify(c.layers)}`;
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return (h >>> 0).toString(36);
};

/**
 * Material oficial liberado nas thumbs: o servidor manda; senão vale o canal (`style_bible.thumbs.allow_editorial`)
 * por cima do estilo (`thumbnail.allow_editorial`, padrão desligado). Espelho de `effective_allow_editorial`.
 */
export const effectiveEditorial = (state: PackageState | null | undefined, style: StyleSpec | null, channel: Channel | null | undefined): boolean => {
  if (state?.allow_editorial != null) return state.allow_editorial;
  const thumbs = channel?.style_bible?.thumbs;
  const fromChannel = isObj(thumbs) && typeof thumbs.allow_editorial === 'boolean' ? thumbs.allow_editorial : null;
  return fromChannel ?? style?.thumbnail.allow_editorial ?? false;
};

/** Texto da thumb dentro das camadas acompanha o `thumb_text` (cria a camada de texto quando falta). */
export const syncTextLayer = (layers: ThumbLayers, thumbText: string): ThumbLayers => {
  const words = thumbText.trim();
  if (!words) return { ...layers, text: layers.text ? { ...layers.text, words: '' } : null };
  return { ...layers, text: { fill: 'yellow', pos: 'left', size: 0.3, ...(layers.text ?? {}), words } };
};

/* ------------------------------------------------------------------ chamadas */

const base = (projectId: string) => `/api/projects/${encodeURIComponent(projectId)}/package`;

export const packageQueryKey = (projectId: string) => ['package', projectId] as const;

export const packageApi = {
  /** GET: o pacote e as referências resolvidas (títulos, multiplicador e miniatura dos outliers). */
  get: async (projectId: string, fallback?: PackageDoc, channelId: string | null = null): Promise<PackageState> =>
    normalizePackageState(await api.get<unknown>(base(projectId)), fallback, channelId),
  /** POST: gera candidatos (job `package`). Os manuais ficam por padrão. */
  generate: (projectId: string, input: GeneratePackageInput = {}) => api.post<{ job?: Job }>(base(projectId), { keep_manual: true, ...input }),
  /** PUT de um candidato: edita título, texto ou camadas; o servidor marca `manual` e re-renderiza a thumb dele. */
  putCandidate: (projectId: string, candidateId: string, patch: CandidatePatch) =>
    api.put<unknown>(`${base(projectId)}/candidates/${encodeURIComponent(candidateId)}`, patch),
  /** POST de um candidato manual (do zero ou duplicando outro com `from_id`). Devolve o id do candidato novo. */
  createCandidate: async (projectId: string, body: CandidatePatch & { from_id?: string }): Promise<string | null> => {
    const raw = await api.post<unknown>(`${base(projectId)}/candidates`, body);
    const cand = isObj(raw) && isObj(raw.candidate) ? raw.candidate : raw;
    return isObj(cand) ? str(cand.id) || null : null;
  },
  deleteCandidate: (projectId: string, candidateId: string) => api.del<unknown>(`${base(projectId)}/candidates/${encodeURIComponent(candidateId)}`),
  /** Re-renderiza as prévias (todas ou só `candidate_ids`). */
  renderThumbs: (projectId: string, candidateIds?: string[]) =>
    api.post<{ job?: Job }>(`${base(projectId)}/thumbs`, candidateIds?.length ? { candidate_ids: candidateIds } : {}),
  /** Escolhido (vai para a capa) e aprovados (entram no A/B). */
  setChoice: (projectId: string, chosenId: string | null, approvedIds: string[]) =>
    api.put<unknown>(`${base(projectId)}/choice`, { chosen_id: chosenId, approved_ids: approvedIds }),
  /** Trava o pacote, copia o texto para a thumb e segue o piloto automático para o roteiro. */
  approve: (projectId: string) => api.post<unknown>(`${base(projectId)}/approve`, {}),
  unlock: (projectId: string) => api.post<unknown>(`${base(projectId)}/unlock`, {}),
  /**
   * Envia uma imagem para a biblioteca de thumbs do canal (multipart `file` + `kind`; recorte e logo só PNG com alfa).
   * `path` é o caminho relativo ao diretório de dados (vira `upload:<path>` no fundo).
   */
  uploadThumbkit: async (channelId: string, file: File, kind: 'frame' | 'cutout' | 'logo' | 'object' = 'frame'): Promise<UploadedThumbkitItem | null> => {
    const form = new FormData();
    form.append('file', file);
    form.append('kind', kind);
    const raw = await api.upload<unknown>(`/api/channels/${encodeURIComponent(channelId)}/thumbkit`, form);
    const item = isObj(raw) && isObj(raw.item) ? raw.item : raw;
    const parsed = normalizeThumbkit([item], channelId)[0] ?? null;
    return parsed ? { ...parsed, path: isObj(item) ? str(item.path) || null : null } : null;
  },
};
