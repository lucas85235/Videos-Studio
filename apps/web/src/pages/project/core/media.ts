// Mídia real das cenas (foto livre, cartão de notícia, material oficial): chamadas das rotas de backend/studio/api/
// routes_media.py e os espelhos das regras de core/project/document.py (mídia mostrada, linha do crédito).

import {
  api,
  type MediaAttachResult,
  type MediaCandidates,
  type MediaCredits,
  type MediaPatch,
  type MediaRef,
  type ProjectDoc,
  type RealHint,
  type RealKind,
  type Scene,
} from '../../../api';
import { reuseRoot } from './model';

const base = (pid: string, sid: string) => `/api/projects/${pid}/scenes/${sid}`;

/** Maior arquivo que o envio aceita (UPLOAD_MAX_BYTES em media/real_media.py). */
export const UPLOAD_MAX_BYTES = 25_000_000;

/** Chave da busca de candidatos de uma cena (`q` vazio: a busca da sugestão do storyboard). */
export const mediaCandidatesKey = (projectId: string, sceneId: string, q?: string) => ['media-candidates', projectId, sceneId, q ?? ''] as const;

export const mediaCandidates = (pid: string, sid: string, q?: string, refresh = false) =>
  api.get<MediaCandidates>(
    `${base(pid, sid)}/media/candidates?${new URLSearchParams({ ...(q ? { q } : {}), ...(refresh ? { refresh: 'true' } : {}) })}`,
  );

export const attachCandidate = (pid: string, sid: string, candidateId: string, kind?: RealKind) =>
  api.post<MediaAttachResult>(`${base(pid, sid)}/media`, { via: 'candidate', candidate_id: candidateId, kind });

export const attachLink = (pid: string, sid: string, url: string, kind?: RealKind | null) =>
  api.post<MediaAttachResult>(`${base(pid, sid)}/media`, { via: 'link', url, kind: kind ?? null });

export const uploadMedia = (pid: string, sid: string, file: File, fields: { kind: RealKind; credit?: string; editorial?: boolean }) => {
  const form = new FormData();
  form.append('file', file);
  form.append('kind', fields.kind);
  if (fields.credit) form.append('credit', fields.credit);
  if (fields.editorial !== undefined) form.append('editorial', String(fields.editorial));
  return api.upload<MediaAttachResult>(`${base(pid, sid)}/media/upload`, form);
};

export const updateMedia = (pid: string, sid: string, patch: MediaPatch) => api.patch<MediaAttachResult>(`${base(pid, sid)}/media`, patch);

export const removeMedia = (pid: string, sid: string) => api.del<{ version: number }>(`${base(pid, sid)}/media`);

export const setRealHint = (pid: string, sid: string, real: RealHint | null) => api.put<{ version: number }>(`${base(pid, sid)}/real`, { real });

export const mediaCredits = (pid: string, lang: string) =>
  api.get<MediaCredits>(`/api/projects/${pid}/media/credits?lang=${encodeURIComponent(lang)}`);

/**
 * Mídia real que a cena mostra: a dela, ou a da cena que ela reaproveita (imagem fixada na própria cena vale mais que
 * a da raiz). Espelho de `ProjectDoc.shown_media`.
 */
export const shownMedia = (byId: ReadonlyMap<string, Scene>, scene: Scene): MediaRef | null => {
  const v = scene.visual;
  if (v.media) return v.media;
  if (v.asset || !v.reuse_of) return null;
  return reuseRoot(byId, scene)?.visual.media ?? null;
};

/** Prefixo do crédito por idioma (CREDIT_PREFIX em document.py): foto livre / outros tipos. */
const CREDIT_PREFIX: Record<string, [string, string]> = {
  pt: ['Foto', 'Imagem'],
  en: ['Photo', 'Image'],
  es: ['Foto', 'Imagen'],
  fr: ['Photo', 'Image'],
  de: ['Foto', 'Bild'],
  it: ['Foto', 'Immagine'],
};

/** Prefixos que o usuário pode ter escrito à mão (saem para não repetir "Foto: Foto: X"). */
const CREDIT_LEADS = [
  'foto:',
  'imagem:',
  'photo:',
  'image:',
  'imagen:',
  'bild:',
  'immagine:',
  'crédito:',
  'credito:',
  'credit:',
  'créditos:',
  'fonte:',
  'source:',
];

/** Crédito na tela no idioma do vídeo ("Foto: Fulano / Wikimedia Commons · CC BY-SA 4.0"); vazio sem crédito. */
export const creditLine = (media: Pick<MediaRef, 'credit' | 'kind'>, lang: string): string => {
  let text = media.credit.split(/\s+/).filter(Boolean).join(' ');
  while (CREDIT_LEADS.some((lead) => text.toLowerCase().startsWith(lead))) text = text.slice(text.indexOf(':') + 1).trim();
  if (!text) return '';
  const [photo, image] = CREDIT_PREFIX[lang.toLowerCase().slice(0, 2)] ?? CREDIT_PREFIX.en;
  return `${media.kind === 'photo' ? photo : image}: ${text}`;
};

/** Cenas com sugestão de mídia real e sem mídia anexada, na ordem do vídeo (como `ProjectDoc.ordered_scenes`). */
export const realSuggestions = (doc: ProjectDoc): Scene[] => {
  const order = new Map(doc.segments.map((seg, i) => [seg.id, i]));
  return doc.scenes
    .filter((s) => Boolean(s.real) && !s.visual.media)
    .sort((a, b) => (order.get(a.segment_id) ?? 1e9) - (order.get(b.segment_id) ?? 1e9) || a.part - b.part);
};

/** Fontes do roteiro com link (viram cartão de notícia ou página para ler a imagem). */
export const linkedSources = (doc: ProjectDoc) =>
  doc.sources.map((s, i) => ({ ...s, n: i + 1 })).filter((s) => /^https?:\/\//i.test(s.url.trim()));

/** Alguma mídia anexada é material de terceiros com direitos (aviso de Content ID). */
export const hasEditorialMedia = (doc: ProjectDoc) => doc.scenes.some((s) => Boolean(s.visual.media?.editorial));

/** Aviso de material oficial (EDITORIAL_WARNING em media/real_media.py). */
export const EDITORIAL_NOTICE = 'Material oficial pode gerar reivindicação de direitos (Content ID). O crédito vai na tela e na descrição.';
