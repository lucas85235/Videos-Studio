// Tipos da timeline gerada pelo backend (studio/core/timeline.py).

/**
 * Palavra da legenda (quadros). `emph`: ênfase da palavra marcada no storyboard (`mark.emphasis`), conforme
 * `grammar.caption_emphasis`; `emoji`: emoji que entra com ela. Ausentes: palavra comum (como antes).
 */
export type CaptionWord = { text: string; from: number; to: number; emph?: CaptionEmphasis; emoji?: string };
export type CaptionEmphasis = 'color' | 'pop' | 'emoji';
export type CaptionLine = { from: number; to: number; words: CaptionWord[] };

export type SceneLook = {
  brightness: number;
  contrast: number;
  saturation: number;
  warmth: number;
  blur: number;
  grade: string | null;
};

/** Nível (origem) do material na tela, do mais real ao gerado (ADR 0004; `core/sources.py`). */
export type Prov = 'own' | 'official' | 'capture' | 'free' | 'stock' | 'graphic' | 'meme' | 'ai';

/** Tipos de evento dentro da cena (espelho de `FxType` em `core/project/document.py`). */
export type FxType =
  | 'punch'
  | 'shake'
  | 'zoom_rect'
  | 'freeze'
  | 'flash'
  | 'text_pop'
  | 'meme'
  | 'sfx'
  | 'highlight'
  | 'arrow'
  | 'circle'
  | 'counter'
  | 'music_drop'
  | 'speed'
  | 'whip'
  | 'pip';

export type ShotEasing = 'linear' | 'outCubic' | 'inOutQuad' | 'snap';

/** Enquadramento do subplano: escala (1 = quadro inteiro) e centro (0 a 1). */
export type ShotFraming = { scale: number; x: number; y: number };

/**
 * Subplano da cena já resolvido (quadros relativos ao início da cena). `src`: imagem (relativa à pasta de dados);
 * `video`: proxy mudo do clipe, tocado de `clipIn` (s) com `speed`, congelando no último quadro com `hold`. Sem `src`
 * nem `video`: o visual da própria cena.
 */
export type TimelineShot = {
  from: number;
  dur: number;
  prov: Prov;
  src?: string;
  video?: string;
  clipIn?: number;
  speed?: number;
  hold?: boolean;
  /**
   * Segundos úteis do clipe a partir de `clipIn` (`MediaRef.dur_s`/`PoolItem.dur_s`). Com `hold`, o plano congela quando
   * eles acabam; sem `hold`, o clipe curto desacelera para cobrir. O backend emite `min(dur_s, official_max_clip_s)`
   * no oficial. Ausente: toca até o fim do plano, salvo `prov: 'official'`, que para em 4 s (rede de segurança).
   */
  seconds?: number;
  /**
   * Proporção (largura ÷ altura) do arquivo do plano (`PoolItem.w/h`, ou do pôster do `MediaRef`): o renderer converte
   * os `rect` dos eventos (espaço do arquivo) para o recorte do plano. Ausente: o retângulo vale como está.
   */
  aspect?: number;
  framing: ShotFraming;
  motion: string;
  easing?: ShotEasing;
  /**
   * Onde há letras no arquivo do plano (`src`, já no tamanho do vídeo): [x, y, w, h] de 0 a 1, detectadas pelo build
   * (`media/text_regions.py`). O carimbo e a faixa do topo desviam delas. Ausente: sem letras conhecidas.
   */
  text_rects?: number[][];
  /**
   * Trecho do arquivo do plano (`[x, y, w, h]`, espaço do arquivo) que nunca sai do quadro: a manchete da captura
   * (`MediaRef.rects`/`PoolItem.rects` unidos). Ausente: sem trecho guardado.
   */
  keep?: number[];
};

/** Evento dentro da cena já resolvido (quadros relativos ao início da cena); `params` depende do tipo. */
export type TimelineFx = { type: FxType; from: number; dur: number; params: Record<string, unknown> };

/**
 * Contrato de áudio da timeline (fica no backend: o som é mixado no FFmpeg, não vai para o renderer). SFX de evento:
 * `at` em segundos no vídeo (o ponto forte, na palavra), `scene` a cena dona, `lib_id` o som da biblioteca e `gain_db`
 * o ganho relativo. `start` (onde o arquivo entra: `at` menos o ataque do tipo) e `scene_id` são as chaves que a
 * mixagem e a conferência de áudio já leem em todo SFX da timeline (os de corte só têm estas).
 */
export type TimelineSfxEvent = {
  type: string;
  at: number;
  scene: string;
  start: number;
  scene_id: string;
  lib_id?: string;
  gain_db?: number;
};
/**
 * Queda da trilha (`timeline.music_drops`, só quando houver): início e duração em segundos no vídeo. A trilha some em
 * `at` e volta em `at + dur`, que é o quadro da palavra-gatilho do evento `music_drop` (a batida do reveal): o
 * silêncio vem ANTES do impacto (ADR 0004 §3.6), nunca depois.
 */
export type TimelineMusicDrop = { at: number; dur: number };

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
 * Reenquadramento de imagem de outra proporção no vídeo (imagem 16:9 do longo no Short em pé). `x`/`y`: ponto de foco na
 * imagem (0 a 1); `aspect`: largura ÷ altura da imagem; `anchor` (só "blur"): altura do centro da imagem inteira.
 */
export type TimelineFrame = { mode: 'crop' | 'blur'; x: number; y: number; aspect: number; anchor?: number };

/**
 * Linha do compare, ponto do gráfico ou região do mapa. `amount`: só em timeline antiga (o backend não manda mais; o
 * gráfico lê o número do `value`, com a unidade e o separador de cada idioma). `region`: código da região do mapa
 * ("BRA", "US-TX").
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
  /** Quadros (relativos à cena) em que a faixa do topo que acusa ("SITE FALSO") entra e sai: só enquanto o plano mostra
   * o material do golpe. Ausentes: a cena inteira. */
  headline_from?: number;
  headline_to?: number;
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
  /** Movimento de câmera; inclui 'zoom_to' (zoom no ponto de foco) e 'parallax' (camadas por profundidade). */
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
  /** Reenquadramento da imagem fixada (Short): vale mais que `focus` no recorte e na câmera. */
  frame?: TimelineFrame;
  /** Stat/compare: quadro (relativo à cena) em que o número do cartão é dito; o cartão entra nele. */
  reveal?: number;
  /** Cartão de notícia (cena de imagem dona da notícia). Ausente: cena sem notícia. */
  news?: TimelineNews;
  /**
   * Variação de família da cena de imagem, sorteada pelo estilo (`edit.treatments`): traço de lousa, recorte de
   * colagem ou dentro de uma tela. Ausente: a imagem com câmera de sempre.
   */
  treatment?: SceneTreatment;
  /** Primeira cena do vídeo na lousa: começa com parte do desenho pronta e desenha rápido (o gancho não fica em branco). */
  treatment_quick?: boolean;
  /**
   * A cena mostra a mesma imagem da anterior com o mesmo tratamento: continua dela (desenho pronto, recorte já na mesa,
   * aparelho já em cena), com a semente da cena que começou (mesma moldura e posição). Ausente: entra do zero.
   */
  treatment_continue?: number;
  /** A mídia é post de rede social: só então a tela (`screen`) pode virar a moldura de post do feed. */
  post?: boolean;
  /**
   * Parallax 2.5D (motion 'parallax'): camadas cortadas pelo mapa de profundidade, do fundo (opaca) para a frente
   * (com transparência), caminhos relativos como `src`. Ausente: a cena usa o Ken Burns.
   */
  parallax?: { layers: string[] };
  /** Vídeo IA pronto da cena-chave (caminho relativo do clipe): toca no lugar da imagem. Ausente: a imagem. */
  video?: string;
  /** Duração do clipe em segundos (para cobrir a cena: um pouco mais lento e, se faltar, o último quadro). */
  video_seconds?: number;
  /** Avatar do apresentador nesta cena (montado pelo `core/timeline.py`). Ausente: sem avatar. */
  presenter?: TimelinePresenter;
  /** Nível do material da cena (`sources.prov_of_scene`). Ausente: timeline antiga. */
  prov?: Prov;
  /** Sem grão nem vinheta nesta cena (efeitos por nível: captura, gráfico e material próprio). Ausente: como antes. */
  clean?: true;
  /** Subplanos da cena (vários planos por batida). Ausente: um plano só, como antes. */
  shots?: TimelineShot[];
  /** Eventos presos a palavras (punch, texto que pula, meme, marca-texto...). Ausente: nenhum. */
  fx?: TimelineFx[];
  /**
   * Onde há letras na imagem da cena (`src`): manchete da captura, logo, letreiro. [x, y, w, h] de 0 a 1 do arquivo,
   * detectadas pelo build (`media/text_regions.py`). Carimbo, faixa do topo e tarja da legenda levam em conta.
   * Ausente: sem letras conhecidas (como antes).
   */
  text_rects?: number[][];
  /**
   * Trecho da imagem da cena (`[x, y, w, h]`, espaço do arquivo) que zoom, pan, punch e zoom no retângulo nunca cortam:
   * a manchete da captura, com `KEEP_MARGIN` em volta (`fx/math.ts`). Ausente: sem trecho guardado.
   */
  keep?: number[];
  /**
   * Cartão sobre imagem: brilho médio (0 a 1) da imagem de fundo. O escurecimento do cartão para no brilho mínimo
   * (`cardDim`), e o fundo nunca vira preto liso. Ausente: o escurecimento do tipo, como antes.
   */
  bg_luma?: number;
  /**
   * Cartão sobre imagem: detalhe (0 a 1, bordas da imagem reduzida) da imagem de fundo. Abaixo de
   * `CARD_BG_MIN_DETAIL`, a imagem é lisa e o cartão fica com o fundo do estilo (`cardBackdropOff`).
   */
  bg_detail?: number;
};

/**
 * Avatar do apresentador numa cena. Quadros relativos ao início da cena; `src` relativo à pasta de dados (o
 * `render_file` de 900 px da pose, servido pelo mesmo `assetBaseUrl`).
 */
export type TimelinePresenter = {
  src: string;
  /** Impressão do arquivo da pose (tamanho e data): troca a URL quando o PNG muda com o mesmo nome. */
  rev?: string;
  /** Quadro de entrada (2 antes da palavra-gatilho, ou 6). */
  from: number;
  /** Duração em quadros. */
  dur: number;
  mode: 'reacao' | 'explica';
  side: 'left' | 'right';
  /** Altura em fração da altura do quadro. */
  height: number;
  /** Contorno branco de adesivo, em px. */
  outline: number;
  /** Soco de escala na entrada (pose "surpreso"). */
  punch?: boolean;
};

export type SceneTreatment = 'whiteboard' | 'collage' | 'screen';

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
