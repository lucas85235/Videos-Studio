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

export type SceneKind = 'image' | 'text' | 'title' | 'count' | 'document' | 'quote' | 'video' | 'stat' | 'compare' | 'chart' | 'map';

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

/**
 * Linha do compare, ponto do gráfico ou região do mapa. `amount`: o número do valor já lido pelo backend ("1,5 BI" →
 * 1.5e9), só no gráfico e no mapa. `region`: código da região do mapa ("BRA", "US-TX").
 */
export type TimelineCardItem = { label: string; value: string; amount?: number; region?: string };

export type ChartType = 'bar' | 'line' | 'pie' | 'donut';

/** Cartão e sobreposições da cena, já no idioma do vídeo (só as chaves preenchidas). */
export type TimelineCard = {
  value?: string;
  label?: string;
  items?: TimelineCardItem[];
  highlight?: number;
  /** Tipo do cartão `chart` (ausente: barras). */
  chart?: ChartType;
  headline?: string;
  stamp?: string;
  source?: string;
  /** Onde fica o carimbo solto, pela composição da imagem: do lado oposto ao sujeito ou embaixo. Ausente: à direita. */
  stamp_at?: StampPlace;
  /** Quadro (relativo à cena) em que o carimbo entra: quando o número dele é dito. Ausente: logo depois do corte. */
  stamp_from?: number;
};

export type StampPlace = 'left' | 'right' | 'low';

/**
 * Cartão de notícia desenhado pelo app (nunca captura de tela), só na cena dona da notícia. Os textos já vêm no idioma
 * do vídeo: `date` formatada ("14 nov. 2026"), `headline` sem aspas (o cartão põe quando `quoted`), `image` é o
 * caminho relativo do og:image original (miniatura dentro do cartão; o `src` da cena é a mesma imagem enquadrada,
 * para o fundo).
 */
export type TimelineNews = {
  outlet: string;
  headline: string;
  date?: string;
  image?: string;
  /** A manchete é a do veículo, literal: vai entre aspas. Traduzida ou editada (ou ausente), sem aspas. */
  quoted?: boolean;
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
  /** Stat/compare: quadro (relativo à cena) em que o número do cartão é dito; o cartão entra nele. */
  reveal?: number;
  /**
   * Crédito da mídia real que a cena mostra (a dela ou a reaproveitada), pronto no idioma do vídeo: "Foto: Fulano /
   * Wikimedia Commons · CC BY-SA 4.0", "Imagem: Rockstar Games". Ausente: imagem de IA ou cartão sem mídia real.
   */
  credit?: string;
  /** Cartão de notícia (cena de imagem dona da notícia). Ausente: cena sem notícia. */
  news?: TimelineNews;
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
  /** Karaokê: cor (opaca) das palavras ainda não ditas. Ausente: a cor do texto puxada para o contorno. */
  dim_color?: string | null;
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
