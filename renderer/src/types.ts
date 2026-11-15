// Tipos da timeline gerada pelo backend (studio/core/timeline.py).

export type CaptionWord = { text: string; from: number; to: number };
export type CaptionLine = { from: number; to: number; words: CaptionWord[] };

export type SceneLook = {
  brightness: number;
  contrast: number;
  saturation: number;
  warmth: number;
  blur: number;
  grade: string | null;
};

export type SceneKind = 'image' | 'text' | 'title' | 'count' | 'document' | 'quote' | 'video' | 'stat' | 'compare';

/** Ponto de foco da câmera (origem do zoom). Ausente ou desconhecido vale 'center'. */
export type FocusPoint = 'center' | 'top' | 'bottom' | 'left' | 'right' | 'top_left' | 'top_right' | 'bottom_left' | 'bottom_right';

export const FOCUS_ORIGIN: Record<FocusPoint, string> = {
  center: '50% 50%',
  top: '50% 22%',
  bottom: '50% 78%',
  left: '22% 50%',
  right: '78% 50%',
  top_left: '22% 22%',
  top_right: '78% 22%',
  bottom_left: '22% 78%',
  bottom_right: '78% 78%',
};

export type TimelineCardItem = { label: string; value: string };

/** Cartão e sobreposições da cena, já no idioma do vídeo (só as chaves preenchidas). */
export type TimelineCard = {
  value?: string;
  label?: string;
  items?: TimelineCardItem[];
  highlight?: number;
  headline?: string;
  stamp?: string;
  source?: string;
};

export type TimelineScene = {
  id: string;
  kind: SceneKind;
  from: number;
  durationInFrames: number;
  src: string | null;
  /** Movimento de câmera; inclui 'zoom_to' (zoom no ponto de foco). */
  motion: string;
  transition: string;
  seed: number;
  look: SceneLook;
  text: string;
  number: number | null;
  captions: CaptionLine[];
  words: CaptionWord[];
  card?: TimelineCard;
  focus?: FocusPoint;
};

export type EffectSpec = { type: string; amount: number; preset?: string | null; params?: Record<string, unknown> };

export type CaptionStyle = {
  style: 'karaoke' | 'word' | 'line' | 'none';
  font: string;
  weight: number;
  size: number;
  color: string;
  highlight: string;
  stroke: number;
  stroke_color: string;
  position: 'top' | 'center' | 'bottom';
  max_words_per_line: number;
  uppercase: boolean;
  background: string | null;
};

export type TextCardStyle = {
  font: string;
  color: string;
  accent: string;
  background: string;
  animation: 'fade_up' | 'typewriter' | 'pop' | 'slide' | 'flicker';
};

export type RenderStyle = {
  family: string;
  families: string[];
  effects: EffectSpec[];
  captions: CaptionStyle;
  textCards: TextCardStyle;
  zoomStrength: number;
  transitionFrames: number;
  progressBar: boolean;
  progressColor: string;
};

export type TimelineProps = {
  fps: number;
  width: number;
  height: number;
  durationInFrames: number;
  style: RenderStyle;
  scenes: TimelineScene[];
  isChunk: boolean;
  lang: string;
  assetBaseUrl?: string | null;
  fontBaseUrl?: string | null;
};
