import React from 'react';
import { AbsoluteFill, Easing, Img, interpolate, random, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { frameLayout } from '../frame';
import { hasEffect, imageFilter } from '../look';
import { type EffectSpec, FOCUS_ORIGIN, type FocusPoint, type ShotEasing, type TextCardStyle, type TimelineScene } from '../types';
import { MotionBackground, withAlpha } from './DataCards';
import { EASE, isEasing } from './fx/math';

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
  /** Curva do movimento (plano com `easing`). Ausente: a de sempre (inOut quad). */
  easing?: ShotEasing;
  /**
   * Camada presa à imagem (marca-texto, seta, círculo): fica dentro do mesmo transform da imagem e anda com a câmera.
   * Ausente: a imagem sozinha, como sempre.
   */
  overlay?: React.ReactNode;
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
export const VisualLayer: React.FC<Props> = ({
  scene,
  effects,
  zoomStrength,
  transitionFrames,
  url,
  layers = [],
  cards,
  dim = 0,
  blur = 0,
  solid = false,
  easing,
  overlay,
}) => {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const dur = Math.max(1, scene.durationInFrames - 1);
  const p = isEasing(easing)
    ? EASE[easing](frame / dur)
    : interpolate(frame, [0, dur], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.inOut(Easing.quad) });
  const z = zoomStrength;
  const side = (r: number) => (random(`${scene.seed}-${r}`) > 0.5 ? 1 : -1);
  const t = frame / fps;
  // O foco é a origem da escala (centro quando não vem). Imagem reenquadrada (Short): o foco dela, onde cai na tela.
  const layout = scene.frame && url ? frameLayout(scene.frame, width, height) : null;
  const origin = layout ? `${layout.origin[0] * 100}% ${layout.origin[1] * 100}%` : focusOrigin(scene.focus);
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
    case 'push':
      // Empurrão: aproxima reto no foco, sem andar de lado (o oposto do Ken Burns que desliza).
      path = { scale: lerp(1 + z * 0.1, 1 + z * 0.75), dx: flat, dy: flat };
      break;
    case 'none':
      // Câmera desligada: o quadro inteiro, parado (captura com anotação, clipe com movimento próprio).
      path = { scale: () => 1, dx: flat, dy: flat };
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
        {overlay}
      </AbsoluteFill>
    );
  }

  const filter = `${imageFilter(effects, scene.look)}${extraFilter}${dim ? ` brightness(${1 - dim})` : ''}${blurPx ? ` blur(${blurPx}px)` : ''}`.trim();

  const chroma = hasEffect(effects, 'chromatic');
  // Aberração cromática numa passada só: a imagem é desenhada uma vez e um filtro SVG separa os canais (antes eram
  // 3 cópias da imagem, cada uma com a cadeia inteira de filtros CSS).
  const chromaId = chroma ? `chroma-${String(scene.id).replace(/[^A-Za-z0-9_-]/g, '')}-${scene.seed}` : null;
  const chromaFilter =
    chroma && chromaId ? (
      <ChromaticFilter id={chromaId} amount={chroma.amount} shift={((4 + 6 * chroma.amount) * Math.min(width, height)) / 1080} />
    ) : null;
  const fullFilter = chromaId ? `${filter} url(#${chromaId})`.trim() : filter;

  // Modo desfocado (imagem inteira sobre o fundo borrado) não tem camadas: fica no caminho normal (Ken Burns).
  if (scene.motion === 'parallax' && layers.length > 1 && !layout?.front) {
    // Camadas por profundidade: a gradação vale para o conjunto (uma cadeia de filtros só). O filtro fica num
    // invólucro sem recorte, que inclui as camadas escaladas além do quadro (bump): o desfoque nasce lá fora, e só o
    // contêiner de fora recorta, como no caminho normal (filtro no contêiner recortado misturaria a borda com o vazio).
    const dir = side(1);
    const stack = layers.map((src, i) => {
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
    });
    return (
      <AbsoluteFill style={{ overflow: 'hidden', backgroundColor: '#000' }}>
        {chromaFilter}
        <AbsoluteFill style={{ transform: jitter ? `translateX(${jitter}px)` : undefined }}>
          <AbsoluteFill style={{ filter: fullFilter || undefined }}>{stack}</AbsoluteFill>
        </AbsoluteFill>
        {overlay}
      </AbsoluteFill>
    );
  }

  // Rede de segurança: o deslocamento já cabe (fitAmplitude); a borda da imagem nunca aparece.
  const room = roomOf(scale);
  tx = Math.max(-(1 - ox) * room, Math.min(ox * room, tx));
  ty = Math.max(-(1 - oy) * room, Math.min(oy * room, ty));

  const transform = `translateX(${jitter}px) scale(${scale}) translate(${tx}%, ${ty}%)`;
  const imgStyle: React.CSSProperties = {
    width: '100%',
    height: '100%',
    objectFit: 'cover',
    objectPosition: layout?.position,
    transform,
    transformOrigin: origin,
    filter: fullFilter,
  };
  const front = layout?.front;
  // Modo desfocado: a imagem inteira sobre ela mesma ampliada, escurecida e desfocada; a câmera move as duas juntas.
  const picture = (pictureStyle: React.CSSProperties) =>
    front ? (
      <div style={{ ...pictureStyle, position: 'absolute', inset: 0, overflow: 'hidden' }}>
        <Img
          src={url}
          style={{
            position: 'absolute',
            inset: 0,
            width: '100%',
            height: '100%',
            objectFit: 'cover',
            objectPosition: layout?.position,
            transform: 'scale(1.12)',
            filter: `blur(${(36 * Math.min(width, height)) / 1080}px) brightness(0.5)`,
          }}
        />
        <Img
          src={url}
          style={{
            position: 'absolute',
            left: `${front.left * 100}%`,
            top: `${front.top * 100}%`,
            width: `${front.width * 100}%`,
            height: `${front.height * 100}%`,
            // contain: se a proporção guardada não for a do arquivo, a imagem nunca sai esticada.
            objectFit: 'contain',
          }}
        />
        {overlay}
      </div>
    ) : overlay ? (
      // Com camada presa: o transform vai num invólucro que leva a imagem e a camada juntas.
      <div style={{ position: 'absolute', inset: 0, transform: pictureStyle.transform, transformOrigin: pictureStyle.transformOrigin }}>
        <Img src={url} style={{ ...pictureStyle, position: 'absolute', inset: 0, transform: undefined, transformOrigin: undefined }} />
        {overlay}
      </div>
    ) : (
      <Img src={url} style={pictureStyle} />
    );
  return (
    <AbsoluteFill style={{ overflow: 'hidden', backgroundColor: '#000' }}>
      {chromaFilter}
      {picture(imgStyle)}
    </AbsoluteFill>
  );
};

/**
 * Filtro SVG da aberração cromática: desloca o vermelho para um lado e o azul para o outro, mantém o verde e mistura
 * com a imagem original pela intensidade. Tudo em sRGB (sem conversão para RGB linear, mais rápido).
 *
 * O deslocamento deixa uma faixa vazia de `shift` px na borda da imagem; ali entra a imagem sem deslocar (feMerge
 * com a SourceGraphic por baixo), senão o vermelho sumiria numa borda e o azul na outra (faixa ciano/amarela quando
 * a borda da imagem encosta no quadro: câmera parada, fim de pan, foco perto da borda).
 *
 * A intensidade começa em 0 e fica perto do visual antigo (cópias coloridas com opacidade 0,32 × intensidade).
 */
const ChromaticFilter: React.FC<{ id: string; amount: number; shift: number }> = ({ id, amount, shift }) => {
  const mix = Math.min(1, Math.max(0, 0.35 * amount));
  return (
    <svg width={0} height={0} style={{ position: 'absolute' }} aria-hidden="true">
      <defs>
        <filter id={id} x="0" y="0" width="100%" height="100%" colorInterpolationFilters="sRGB">
          <feOffset in="SourceGraphic" dx={shift} dy={0} result="rSo" />
          <feMerge result="rDesloc">
            <feMergeNode in="SourceGraphic" />
            <feMergeNode in="rSo" />
          </feMerge>
          <feColorMatrix in="rDesloc" type="matrix" values="1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0" result="r" />
          <feOffset in="SourceGraphic" dx={-shift} dy={0} result="bSo" />
          <feMerge result="bDesloc">
            <feMergeNode in="SourceGraphic" />
            <feMergeNode in="bSo" />
          </feMerge>
          <feColorMatrix in="bDesloc" type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 1 0 0  0 0 0 1 0" result="b" />
          <feColorMatrix in="SourceGraphic" type="matrix" values="0 0 0 0 0  0 1 0 0 0  0 0 0 0 0  0 0 0 1 0" result="g" />
          <feComposite in="r" in2="g" operator="arithmetic" k1={0} k2={1} k3={1} k4={0} result="rg" />
          <feComposite in="rg" in2="b" operator="arithmetic" k1={0} k2={1} k3={1} k4={0} result="separado" />
          <feComposite in="separado" in2="SourceGraphic" operator="arithmetic" k1={0} k2={mix} k3={1 - mix} k4={0} />
        </filter>
      </defs>
    </svg>
  );
};
