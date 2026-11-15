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
