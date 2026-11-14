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

export type SceneKind = 'image' | 'text' | 'title' | 'count' | 'document' | 'quote' | 'video';

export type TimelineScene = {
  id: string;
  kind: SceneKind;
  from: number;
  durationInFrames: number;
  src: string | null;
  motion: string;
  transition: string;
  seed: number;
  look: SceneLook;
  text: string;
  number: number | null;
  captions: CaptionLine[];
  words: CaptionWord[];
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
