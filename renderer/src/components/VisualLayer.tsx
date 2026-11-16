import React from 'react';
import { AbsoluteFill, Easing, Img, interpolate, random, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { hasEffect, imageFilter } from '../look';
import { type EffectSpec, FOCUS_ORIGIN, type FocusPoint, type TextCardStyle, type TimelineScene } from '../types';
import { MotionBackground, withAlpha } from './DataCards';

type Props = {
  scene: TimelineScene;
  effects: EffectSpec[];
  zoomStrength: number;
  transitionFrames: number;
  url: string | null;
  /** Parallax 2.5D: camadas do fundo para a frente (motion 'parallax'). Vazio: a cena usa o Ken Burns. */
  layers?: string[];
  /** Cores do estilo para o fundo animado quando a cena não tem imagem. */
  cards: TextCardStyle;
  dim?: number;
  /** Desfoque em px (referência 1080 no menor lado), ex.: fundo dos cartões de dado. */
  blur?: number;
  /** Sem imagem, cobre o fundo animado com a cor de fundo do estilo quase opaca (contraste do texto cinético). */
  solid?: boolean;
};

const focusOrigin = (focus: string | undefined) =>
  focus && Object.prototype.hasOwnProperty.call(FOCUS_ORIGIN, focus) ? FOCUS_ORIGIN[focus as FocusPoint] : FOCUS_ORIGIN.center;

/** Caminho de um movimento de câmera: escala e deslocamento (% do quadro) em função do progresso q (0..1). */
type CameraPath = { scale: (q: number) => number; dx: (q: number) => number; dy: (q: number) => number };

const flat = () => 0;

/** Espaço (em % do quadro) que a escala deixa para deslocar a imagem sem mostrar a borda. */
const roomOf = (scale: number) => (Math.max(1, scale) - 1) / Math.max(1, scale) * 100;

/**
 * Fração (0..1) da amplitude do movimento que cabe em todo o caminho, dado o foco (origem da escala). Com o foco fora
 * do centro, o lado mais curto limita: o movimento fica menor, mas anda até o fim em vez de travar na borda.
 */
const fitAmplitude = (samples: { scale: number; dx: number; dy: number }[], ox: number, oy: number) => {
  let k = 1;
  for (const { scale, dx, dy } of samples) {
    const room = roomOf(scale);
    if (Math.abs(dx) > 1e-9) k = Math.min(k, ((dx > 0 ? ox : 1 - ox) * room) / Math.abs(dx));
    if (Math.abs(dy) > 1e-9) k = Math.min(k, ((dy > 0 ? oy : 1 - oy) * room) / Math.abs(dy));
  }
  return Math.max(0, k);
};

/**
 * Parallax 2.5D: aproxima devagar e anda de lado, com cada camada (0 = fundo) andando mais quanto mais perto ela
 * está. Escala e deslocamento de cada camada em função do progresso q (0..1); `side` sorteia o lado pela semente.
 */
export const parallaxLayerPath = (index: number, count: number, z: number, side: number, q: number) => {
  const w = count > 1 ? index / (count - 1) : 0;
  return {
    scale: (1 + z * 0.6) * (1 + z * q * (0.1 + 0.55 * w)),
    dx: side * z * 36 * (q - 0.5) * (0.12 + w),
    dy: -z * 8 * q * w,
  };
};

/** Imagem com movimento de câmera (Ken Burns, pan, punch zoom, drift, zoom no foco, parallax) e gradação de cor. */
export const VisualLayer: React.FC<Props> = ({ scene, effects, zoomStrength, transitionFrames, url, layers = [], cards, dim = 0, blur = 0, solid = false }) => {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const dur = Math.max(1, scene.durationInFrames - 1);
  const p = interpolate(frame, [0, dur], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.inOut(Easing.quad) });
  const z = zoomStrength;
  const side = (r: number) => (random(`${scene.seed}-${r}`) > 0.5 ? 1 : -1);
  const t = frame / fps;
  // O foco é a origem da escala (centro quando não vem).
  const origin = focusOrigin(scene.focus);
  const [ox, oy] = origin.split(' ').map((v) => parseFloat(v) / 100);
  const blurPx = blur > 0 ? (blur * Math.min(width, height)) / 1080 : 0;
  const bump = blurPx ? 1 + (5 * blurPx) / Math.min(width, height) : 1; // a borda desfocada fica fora do quadro
  const lerp = (a: number, b: number) => (q: number) => interpolate(q, [0, 1], [a, b]);

  let path: CameraPath;
  // Drift: deslocamento pelo tempo (seno), não pelo progresso; a conferência usa os extremos.
  let drift: { ax: number; ay: number } | null = null;
  switch (scene.motion) {
    case 'parallax': // sem camadas (modelo ausente) é o Ken Burns; com camadas, o caminho fica no ParallaxStack
    case 'ken_burns_in':
      path = { scale: lerp(1 + z * 0.15, 1 + z), dx: (q) => side(1) * lerp(0, z * 12)(q), dy: flat };
      break;
    case 'ken_burns_out':
      path = { scale: lerp(1 + z, 1 + z * 0.15), dx: flat, dy: (q) => side(2) * lerp(z * 10, 0)(q) };
      break;
    case 'pan_left':
      path = { scale: () => 1 + z, dx: lerp(z * 30, -z * 30), dy: flat };
      break;
    case 'pan_right':
      path = { scale: () => 1 + z, dx: lerp(-z * 30, z * 30), dy: flat };
      break;
    case 'pan_up':
      path = { scale: () => 1 + z, dx: flat, dy: lerp(z * 30, -z * 30) };
      break;
    case 'pan_down':
      path = { scale: () => 1 + z, dx: flat, dy: lerp(-z * 30, z * 30) };
      break;
    case 'punch_zoom': {
      const punch = spring({ frame, fps, config: { damping: 14, stiffness: 160, mass: 0.6 } });
      const s = 1 + z * 0.1 + punch * z * 0.55 + p * z * 0.2;
      path = { scale: () => s, dx: flat, dy: flat };
      break;
    }
    case 'drift':
      drift = { ax: z * 14, ay: z * 10 };
      path = {
        scale: () => 1 + z * 0.55,
        dx: () => Math.sin(t * 0.45 + scene.seed) * z * 14,
        dy: () => Math.cos(t * 0.37 + scene.seed * 0.5) * z * 10,
      };
      break;
    case 'zoom_to':
      // Zoom no detalhe: aproxima do ponto de foco (transformOrigin).
      path = { scale: lerp(1 + z * 0.2, 1 + Math.min(0.35, 2.5 * z)), dx: flat, dy: flat };
      break;
    case 'static':
    default:
      path = { scale: () => 1 + z * 0.1, dx: flat, dy: flat };
  }
  const samples = drift
    ? [-1, 1].flatMap((sx) => [-1, 1].map((sy) => ({ scale: path.scale(0) * bump, dx: sx * drift!.ax, dy: sy * drift!.ay })))
    : Array.from({ length: 21 }, (_, i) => i / 20).map((q) => ({ scale: path.scale(q) * bump, dx: path.dx(q), dy: path.dy(q) }));
  const k = fitAmplitude(samples, ox, oy);
  let scale = path.scale(p) * bump;
  let tx = path.dx(p) * k;
  let ty = path.dy(p) * k;

  // Transições de entrada que mexem na imagem
  const tf = Math.max(1, transitionFrames);
  let extraFilter = '';
  let entryScale = 1;
  if (scene.transition === 'zoom_blur' && frame < tf) {
    const k = 1 - frame / tf;
    entryScale = 1 + 0.14 * k;
    scale *= entryScale;
    extraFilter = ` blur(${10 * k}px)`;
  }
  let jitter = 0;
  if (scene.transition === 'glitch' && frame < tf) {
    jitter = (random(`${scene.seed}-g-${frame}`) - 0.5) * 60;
  }
  const vhs = hasEffect(effects, 'vhs');
  if (vhs) {
    jitter += (random(`${scene.seed}-v-${frame}`) - 0.5) * 6 * vhs.amount;
  }

  if (!url) {
    // Sem imagem (cartão ou referência solta): fundo animado nas cores do estilo.
    const bgFilter = `${imageFilter(effects, { ...scene.look, blur: 0 })}${dim ? ` brightness(${1 - dim * 0.35})` : ''}`.trim();
    return (
      <AbsoluteFill>
        <AbsoluteFill style={{ filter: bgFilter || undefined }}>
          <MotionBackground style={cards} seed={scene.seed} />
        </AbsoluteFill>
        {solid ? <AbsoluteFill style={{ backgroundColor: withAlpha(cards.background, 0.85) }} /> : null}
      </AbsoluteFill>
    );
  }

  const filter = `${imageFilter(effects, scene.look)}${extraFilter}${dim ? ` brightness(${1 - dim})` : ''}${blurPx ? ` blur(${blurPx}px)` : ''}`.trim();

  if (scene.motion === 'parallax' && layers.length > 1) {
    // Camadas por profundidade: a gradação vale para o conjunto (uma cadeia de filtros só, no contêiner).
    const dir = side(1);
    return (
      <AbsoluteFill style={{ overflow: 'hidden', backgroundColor: '#000', filter: filter || undefined, transform: jitter ? `translateX(${jitter}px)` : undefined }}>
        {layers.map((src, i) => {
          const lp = parallaxLayerPath(i, layers.length, z, dir, p);
          const s = lp.scale * bump * entryScale;
          const r = roomOf(s);
          const lx = Math.max(-(1 - ox) * r, Math.min(ox * r, lp.dx));
          const ly = Math.max(-(1 - oy) * r, Math.min(oy * r, lp.dy));
          return (
            <Img
              key={src}
              src={src}
              style={{
                position: 'absolute',
                inset: 0,
                width: '100%',
                height: '100%',
                objectFit: 'cover',
                transformOrigin: origin,
                transform: `scale(${s}) translate(${lx}%, ${ly}%)`,
              }}
            />
          );
        })}
      </AbsoluteFill>
    );
  }

  // Rede de segurança: o deslocamento já cabe (fitAmplitude); a borda da imagem nunca aparece.
  const room = roomOf(scale);
  tx = Math.max(-(1 - ox) * room, Math.min(ox * room, tx));
  ty = Math.max(-(1 - oy) * room, Math.min(oy * room, ty));

  const transform = `translateX(${jitter}px) scale(${scale}) translate(${tx}%, ${ty}%)`;
  const chroma = hasEffect(effects, 'chromatic');
  const imgStyle: React.CSSProperties = { width: '100%', height: '100%', objectFit: 'cover', transform, transformOrigin: origin, filter };
  return (
    <AbsoluteFill style={{ overflow: 'hidden', backgroundColor: '#000' }}>
      <Img src={url} style={imgStyle} />
      {chroma ? (
        <>
          <Img
            src={url}
            style={{
              ...imgStyle,
              position: 'absolute',
              inset: 0,
              mixBlendMode: 'screen',
              opacity: 0.32 * chroma.amount,
              transform: `${transform} translateX(${4 + 6 * chroma.amount}px)`,
              filter: `${filter} grayscale(1) sepia(1) saturate(6) hue-rotate(-40deg)`,
            }}
          />
          <Img
            src={url}
            style={{
              ...imgStyle,
              position: 'absolute',
              inset: 0,
              mixBlendMode: 'screen',
              opacity: 0.32 * chroma.amount,
              transform: `${transform} translateX(-${4 + 6 * chroma.amount}px)`,
              filter: `${filter} grayscale(1) sepia(1) saturate(6) hue-rotate(160deg)`,
            }}
          />
        </>
      ) : null}
    </AbsoluteFill>
  );
};
