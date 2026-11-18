/* Cliente do bloco G (ADR 0003): conferência de áudio sem renderizar e curadoria das trilhas. */
import { api, type Job } from './api';
import type { QualityReport } from './apiChecks';

/** Medidas de `music_curate` de uma faixa (chaves do canais). */
export type MusicCuration = {
  ok: boolean;
  /** Por que não há trecho de loop (ex.: "faixa curta demais para escolher um trecho"). */
  reason?: string;
  dur_s?: number;
  bpm?: number;
  loop_start_s?: number;
  loop_s?: number;
  loop_bars?: number;
  bar_s?: number;
  /** Semelhança da batida na emenda (0 a 1). */
  emenda_batida?: number;
  /** Semelhança da onda na emenda (0 a 1). */
  emenda_onda?: number;
  /** Banda da voz (1 a 4 kHz) relativa ao volume do trecho, em dB. */
  voz_db?: number;
  voz_vs_pico_db?: number;
  variacao_db?: number;
  comeco_ok_s?: number;
  /** Planura de 400 Hz a 3 kHz (0 = notas isoladas, 1 = faixa cheia). */
  textura?: number;
  intro?: boolean;
  trim_db?: number;
  /** WAV do loop (relativo à pasta de dados). */
  loop?: string;
  override?: Record<string, unknown>;
};

/** Curadoria por faixa da biblioteca (chave = caminho da faixa). */
export type MusicCurationMap = Record<string, MusicCuration>;

export type SfxGuardItem = {
  kind: string;
  dur: number;
  prom: number;
  hz: number;
  tonal_ms: number;
  apito: boolean;
  tonal_fraction?: number;
  tonal_hz?: number;
  banned?: boolean;
  /** Fica fora do mix (apito ou banido). */
  blocked?: boolean;
};

export type SfxGuard = { items: SfxGuardItem[]; ok: boolean };

/** Linha por capítulo da conferência de áudio (em `QualityReport.data.chapters`). */
export type AudioChapterLevel = {
  title: string;
  start_s: number;
  end_s: number;
  voice_db: number | null;
  music_speech_db: number | null;
  music_pause_db: number | null;
  distance_db: number | null;
};

export type AudioSfxEvent = {
  t: number;
  kind: string | null;
  scene_id: string | null;
  lufs: number;
  vs_voz: number | null;
  tom: number;
  tom_hz: number;
};

export type MusicBed = {
  cue_id: string | null;
  track: string | null;
  mood: string | null;
  start_s: number;
  dur_s: number;
  loop: [number, number] | null;
};

export type AudioCheckData = {
  chapters?: AudioChapterLevel[];
  episode?: AudioChapterLevel | null;
  sfx_events?: AudioSfxEvent[];
  sfx_files?: SfxGuardItem[];
  music?: { source: string; track: string | null; mood: string | null; volume_db: number; beds?: MusicBed[] } | null;
  voice_gain_db?: number;
};

export type AudioCheckState = { report: QualityReport | null };
export type AudioCheckInput = { lang?: string; source?: 'preview' | 'final' };

/** Faixa de música do documento com o campo `loop` (ADR 0003): "só a batida, em loop no compasso". */
export type MusicCueDoc = {
  id: string;
  start_segment: string | null;
  mood: string | null;
  volume_db: number | null;
  enabled: boolean;
  track?: string | null;
  loop?: boolean;
};

/** Distância música × voz que a regra 12 do estúdio pede (dB). */
export const MUSIC_DISTANCE: [number, number] = [14, 19];

export const audioApi = {
  curation: () => api.get<MusicCurationMap>('/api/library/music/curation'),
  curate: (tracks: string[] = [], force = false) => api.post<Job>('/api/library/music/curate', { tracks, force }),
  /** Espectrograma PNG da faixa (imagem, não JSON): use como `src`. */
  spectroUrl: (track: string, dur = 100) => `/api/library/music/spectro?track=${encodeURIComponent(track)}&dur=${dur}`,
  sfxGuard: () => api.get<SfxGuard>('/api/sfx/guard'),
  audioCheck: (projectId: string, lang: string) =>
    api.get<AudioCheckState>(`/api/projects/${projectId}/audio-check?lang=${encodeURIComponent(lang)}`),
  runAudioCheck: (projectId: string, body: AudioCheckInput = {}) => api.post<Job>(`/api/projects/${projectId}/audio-check`, body),
};

/* ------------------------------------------------------------------ */
/* Biblioteca de efeitos e memes (ADR 0004 §3.8, backend/studio/api/routes_library.py) */
/* ------------------------------------------------------------------ */

export type LibraryLicense = { id: string; label: string };

/** Um efeito da biblioteca (`SfxItem.public`): embutido (procedural) ou do usuário. */
export type SfxItem = {
  id: string;
  file: string | null;
  kind: string;
  tags: string[];
  license: 'own' | 'cc0' | 'mixkit-free' | 'cc_by' | 'desconhecida';
  license_label: string;
  source_url: string;
  author: string;
  /** Ponto forte (s desde o início do efeito preparado): a palavra do evento cai nele. */
  hit_s: number;
  dur: number;
  lufs: number | null;
  tonal: boolean;
  tonal_share: number;
  apito: boolean;
  origin: 'builtin' | 'user';
  imported_from: string;
  note: string;
  procedural: boolean;
  /** Entra no automático (planejador e escolha por tipo). */
  auto: boolean;
  /** O arquivo sumiu da pasta (toca o efeito do tipo no lugar). */
  missing: boolean;
  warning: string | null;
};

export type SfxLibrary = {
  dir: string;
  items: SfxItem[];
  warnings: string[];
  /** Arquivos soltos na pasta ainda não importados (entram pelo "Procurar arquivos soltos"). */
  loose: string[];
  licenses: LibraryLicense[];
  procedural: string[];
  freesound: { fields: Record<string, string>; from_env: string | null; configured: boolean };
  offline: boolean;
};

export type SfxPatch = Partial<Pick<SfxItem, 'license' | 'kind' | 'tags' | 'author' | 'source_url' | 'note' | 'hit_s'>>;

export type MemeItem = {
  id: string;
  file: string;
  url: string;
  media: 'png' | 'webm';
  tags: string[];
  license: 'own' | 'cc0' | 'cc_by' | 'fair_comment';
  license_label: string;
  risk: 'low' | 'medium' | 'high';
  max_ms: number;
  sfx_id: string | null;
  author: string;
  source_url: string;
  origin: 'user' | 'presenter';
  auto: boolean;
  warning: string | null;
};

export type MemeLibrary = {
  dir: string;
  items: MemeItem[];
  warnings: string[];
  templates: { id: string; label: string; tags: string[] }[];
  licenses: LibraryLicense[];
  risks: MemeItem['risk'][];
};

export type CanaisSfxReport = {
  source: string;
  imported: { file: string; id: string; kind: string; license: string; auto?: boolean }[];
  skipped: { file: string; reason: string }[];
  warnings: string[];
};

export const libraryApi = {
  sfx: () => api.get<SfxLibrary>('/api/library/sfx'),
  /** Upload multipart: `file`, `kind`, `license`, `tags`, `author`, `source_url`. */
  uploadSfx: (form: FormData) => api.upload<SfxItem>('/api/library/sfx', form),
  patchSfx: (id: string, body: SfxPatch) => api.patch<SfxItem>(`/api/library/sfx/${encodeURIComponent(id)}`, body),
  deleteSfx: (id: string) => api.del<{ deleted: string }>(`/api/library/sfx/${encodeURIComponent(id)}`),
  /** O efeito como entra no mix (WAV): use como `src` de um <audio>. */
  sfxAudioUrl: (id: string) => `/api/library/sfx/${encodeURIComponent(id)}/audio`,
  /** Job `sfx_scan`: os arquivos soltos na pasta entram como licença desconhecida. */
  scanSfx: () => api.post<Job>('/api/library/sfx/scan'),
  fetchFreesound: (body: { sound: string; kind: string; tags?: string[] }) => api.post<SfxItem>('/api/library/sfx/fetch-freesound', body),
  setFreesoundKey: (apiKey: string) => api.put<SfxLibrary['freesound']>('/api/library/freesound-key', { api_key: apiKey }),
  deleteFreesoundKey: () => api.del<SfxLibrary['freesound']>('/api/library/freesound-key'),
  /** Job `sfx_voice`: "ué", "pera"... com a voz final do canal (422 na hora sem a voz final). */
  voiceSfx: (body: { channel_id?: string | null; style_id?: string | null; lang?: string }) => api.post<Job>('/api/library/sfx/voice', body),
  canaisPreview: (licenseChecked: boolean) =>
    api.post<CanaisSfxReport>('/api/library/sfx/import-canais', { dry_run: true, license_checked: licenseChecked }),
  /** Job `sfx_import_canais`. */
  importCanais: (body: { replace?: boolean; license_checked?: boolean }) => api.post<Job>('/api/library/sfx/import-canais', body),
  memes: (channelId?: string | null) => api.get<MemeLibrary>(`/api/library/memes${channelId ? `?channel_id=${encodeURIComponent(channelId)}` : ''}`),
  /** Upload multipart: `file` (PNG ou WebM), `license` (obrigatória), `tags`, `risk`, `max_ms`, `author`, `source_url`. */
  uploadMeme: (form: FormData) => api.upload<MemeItem>('/api/library/memes', form),
  deleteMeme: (id: string) => api.del<{ deleted: string }>(`/api/library/memes/${encodeURIComponent(id)}`),
};
