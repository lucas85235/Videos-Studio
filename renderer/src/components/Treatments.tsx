// Variações de família para a cena de imagem (README §5, famílias 4, 5 e 8). O backend sorteia o tratamento pelos
// pesos do estilo (`edit.treatments`, ou automático pela família) e manda `scene.treatment`; sem ele a cena usa a
// imagem com câmera de sempre (VisualLayer).
//
// - whiteboard: lousa branca; a imagem vai sendo revelada por um traço largo em zigue-zague (clip-path), com a
//   caneta na ponta do traço, e a cor entra quando o desenho termina.
// - collage: a imagem vira um recorte de papel (borda branca rasgada, sombra, fita adesiva) sobre papel kraft,
//   com o tremor de stop motion (8 poses por segundo).
// - screen: a imagem aparece dentro de uma janela, de um celular ou de um post de feed, com cursor que aponta o
//   foco da cena e clica. A interface não tem texto legível (só blocos).
//
// Tudo é CSS/SVG desenhado aqui (sem imagem extra): a <Img> do Remotion espera a imagem carregar antes do quadro.

import React from 'react';
import { AbsoluteFill, Easing, Img, interpolate, random, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { hasEffect, imageFilter } from '../look';
import { keepTransform, pRects, type PxRect, rectThrough, screenFrame } from './fx/math';
import { type EffectSpec, FOCUS_ORIGIN, type FocusPoint, type SceneTreatment, type TextCardStyle, type TimelineScene } from '../types';

export type TreatmentProps = {
  scene: TimelineScene;
  effects: EffectSpec[];
  zoomStrength: number;
  url: string;
  cards: TextCardStyle;
  transitionFrames: number;
};

const clamp = { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' } as const;

/** Tratamentos que o renderer desenha; outro valor (ou ausente) cai na imagem com câmera. */
export const TREATMENT_IDS: readonly SceneTreatment[] = ['whiteboard', 'collage', 'screen'];

export const isTreatment = (value: unknown): value is SceneTreatment =>
  typeof value === 'string' && (TREATMENT_IDS as readonly string[]).includes(value);

/** Foco da cena em fração (0..1) do quadro da imagem. */
export const focusXY = (focus: string | undefined): [number, number] => {
  const origin = focus && Object.prototype.hasOwnProperty.call(FOCUS_ORIGIN, focus) ? FOCUS_ORIGIN[focus as FocusPoint] : FOCUS_ORIGIN.center;
  const [x, y] = origin.split(' ').map((v) => parseFloat(v) / 100);
  return [x, y];
};

/** Semente da moldura: a da cena que começou o tratamento quando esta continua dela (mesma posição e aparelho). */
const layoutSeed = (scene: TimelineScene) => (typeof scene.treatment_continue === 'number' ? scene.treatment_continue : scene.seed);

/** Zoom lento da imagem dentro da moldura (não usa o pan: a moldura já mexe). `static` fica quase parado. */
const innerScale = (motion: string, z: number, p: number) => {
  if (motion === 'static') return 1 + z * 0.1;
  if (motion === 'ken_burns_out') return interpolate(p, [0, 1], [1 + z, 1 + z * 0.15]);
  return interpolate(p, [0, 1], [1 + z * 0.15, 1 + z]);
};

/**
 * Zoom lento da imagem dentro da moldura (lousa, recorte, tela) sem cortar a manchete da captura (`scene.keep`): o
 * `transform` CSS (escala em volta da origem `origin`, em fração do contêiner, mais o deslocamento que a manchete
 * pedir; a escala desce se ela não couber). A moldura tem a proporção do vídeo (ou perto dela): o retângulo do
 * arquivo vale como está.
 */
export const keptZoom = (scene: TimelineScene, scale: number, origin: [number, number]) => {
  const keep = pRects({ rect: scene.keep })[0];
  const kept = keepTransform(keep, scale, origin, [0, 0]);
  const [tx, ty] = kept.t;
  const shift = Math.abs(tx) > 1e-6 || Math.abs(ty) > 1e-6 ? ` translate(${tx * 100}%, ${ty * 100}%)` : '';
  return { scale: kept.s, t: kept.t, transform: `scale(${kept.s})${shift}` };
};

const useProgress = (scene: TimelineScene) => {
  const frame = useCurrentFrame();
  const dur = Math.max(1, scene.durationInFrames - 1);
  return interpolate(frame, [0, dur], [0, 1], { ...clamp, easing: Easing.inOut(Easing.quad) });
};

// ---------------------------------------------------------------------------------------------
// Whiteboard

/**
 * Quadros do desenho: ~60% da cena, entre 0,8 s e 4 s, e sempre alguns quadros de imagem pronta no fim. `quick`
 * (primeira cena do vídeo): no máximo 1,2 s.
 */
export const drawFrames = (durationInFrames: number, fps: number, quick = false) => {
  const wanted = Math.min(fps * (quick ? 1.2 : 4), Math.max(fps * 0.8, durationInFrames * 0.6));
  return Math.max(1, Math.round(Math.min(wanted, durationInFrames - Math.min(8, durationInFrames * 0.2))));
};

/** Onde o traço começa na primeira cena: ela já abre com parte do desenho (o primeiro quadro não fica em branco). */
export const QUICK_START = 0.3;

/** Progresso do desenho no quadro: a continuação já vem pronta; a primeira cena começa em `QUICK_START`. */
export const drawProgress = (frame: number, total: number, opts: { quick?: boolean; cont?: boolean } = {}) => {
  if (opts.cont) return 1;
  const from = opts.quick ? QUICK_START : 0;
  return interpolate(frame, [0, total], [from, 1], { ...clamp, easing: Easing.inOut(Easing.sin) });
};

/** Faixas (linhas do traço) da lousa: mais no vertical, para o traço ter a mesma grossura. */
const boardRows = (width: number, height: number) => (height > width ? 8 : 6);

/**
 * Contorno (CSS `path()`) da área já desenhada com progresso `q` (0..1): as linhas completas em cima e a linha atual
 * até a ponta do traço, com ponta redonda. As linhas alternam o sentido (esquerda→direita, direita→esquerda) e as
 * bordas ondulam um pouco, como pincelada.
 */
export const revealPath = (q: number, width: number, height: number, seed: number) => {
  const rows = boardRows(width, height);
  const rowH = height / rows;
  const pad = rowH * 0.6; // o traço passa da borda: a linha começa e termina fora do quadro
  const total = rows * (width + 2 * pad);
  const done = Math.max(0, Math.min(1, q)) * total;
  if (done >= total - 1e-6) return { d: `M ${-10} ${-10} H ${width + 10} V ${height + 10} H ${-10} Z`, tip: null as null | [number, number] };
  const row = Math.min(rows - 1, Math.floor(done / (width + 2 * pad)));
  const along = done - row * (width + 2 * pad) - pad; // posição na linha (-pad..width+pad)
  const ltr = row % 2 === 0;
  const tipX = ltr ? along : width - along;
  const yTop = row * rowH;
  const yBot = yTop + rowH;
  const amp = rowH * 0.05;
  // A fase depende da altura: a borda de baixo de uma linha é a mesma de cima da seguinte (não pula na troca).
  const wave = (x: number, y: number) => y + amp * Math.sin(x / (rowH * 0.9) + seed * 0.7 + (y / rowH) * 1.3);
  // Pontos da borda ondulada entre xa e xb (exclusive xa) na altura y.
  const edge = (xa: number, xb: number, y: number) => {
    const n = Math.max(2, Math.ceil(Math.abs(xb - xa) / (rowH * 0.5)));
    const pts: string[] = [];
    for (let i = 1; i <= n; i++) {
      const x = xa + ((xb - xa) * i) / n;
      pts.push(`L ${x.toFixed(1)} ${wave(x, y).toFixed(1)}`);
    }
    return pts.join(' ');
  };
  const r = rowH / 2;
  const top = row === 0 ? -10 : yTop;
  const start = `M ${-10} ${-10} H ${width + 10}`;
  let d: string;
  if (ltr) {
    // Linhas de cima inteiras; na atual, da esquerda até a ponta.
    d = `${start} V ${top} ${row === 0 ? `H ${tipX.toFixed(1)}` : edge(width + 10, tipX, yTop)} V ${yTop} A ${r} ${r} 0 0 1 ${tipX.toFixed(1)} ${yBot} ${edge(tipX, -10, yBot)} Z`;
  } else {
    // Linhas de cima inteiras; na atual, da direita até a ponta.
    d = `${start} V ${wave(width + 10, yBot).toFixed(1)} ${edge(width + 10, tipX, yBot)} A ${r} ${r} 0 0 1 ${tipX.toFixed(1)} ${yTop} ${row === 0 ? `H ${-10} V ${-10}` : edge(tipX, -10, yTop)} Z`;
  }
  return { d, tip: [tipX, yTop + r] as [number, number] };
};

/** Caneta de lousa com a ponta em (0,0), inclinada para a direita. */
const Marker: React.FC<{ x: number; y: number; size: number; tilt: number }> = ({ x, y, size, tilt }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 100 100"
    style={{ position: 'absolute', left: x, top: y - size, overflow: 'visible', transform: `rotate(${tilt}deg)`, transformOrigin: '0 100%', filter: 'drop-shadow(6px 10px 8px rgba(0,0,0,0.25))' }}
  >
    {/* ponta, corpo, anel e tampa traseira */}
    <polygon points="0,100 6,82 18,94" fill="#1f1f1f" />
    <polygon points="6,82 18,94 34,78 22,66" fill="#2d2d2d" />
    <polygon points="22,66 34,78 88,24 76,12" fill="#f3f3f0" stroke="#bdbdb8" strokeWidth="1.5" />
    <polygon points="30,58 42,70 48,64 36,52" fill="#1d4ed8" />
    <polygon points="76,12 88,24 96,16 84,4" fill="#1d4ed8" />
  </svg>
);

const Whiteboard: React.FC<TreatmentProps> = ({ scene, effects, zoomStrength, url }) => {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const p = useProgress(scene);
  const cont = typeof scene.treatment_continue === 'number';
  const seed = layoutSeed(scene);
  const total = drawFrames(scene.durationInFrames, fps, scene.treatment_quick);
  const q = drawProgress(frame, total, { quick: scene.treatment_quick, cont });
  const { d, tip } = revealPath(q, width, height, seed);
  // A cor entra depois do desenho; até lá, traço em tons de cinza. Na continuação, já colorido.
  const color = cont ? 1 : interpolate(frame, [total, total + Math.round(fps * 0.5)], [0, 1], clamp);
  const base = imageFilter(effects, scene.look);
  const filter = `${base} grayscale(${1 - color}) contrast(${1.15 - 0.15 * color})`.trim();
  const scale = 1 + (innerScale(scene.motion, zoomStrength, p) - 1) * 0.5;
  const unit = Math.min(width, height);
  // A caneta sai pela direita/baixo quando termina.
  const away = cont ? 1 : interpolate(frame, [total, total + 10], [0, 1], { ...clamp, easing: Easing.in(Easing.quad) });
  const last = revealPath(Math.min(1, q - 1e-4), width, height, seed).tip ?? [width * 0.9, height * 0.9];
  const pen = tip ?? last;
  const bob = Math.sin(frame * 1.7 + seed) * unit * 0.004;
  const origin = FOCUS_ORIGIN[(scene.focus ?? 'center') as FocusPoint] ?? '50% 50%';
  const zoom = keptZoom(scene, scale, focusXY(scene.focus));
  return (
    <AbsoluteFill style={{ backgroundColor: '#FBFBF8', overflow: 'hidden' }}>
      <AbsoluteFill
        style={{ background: 'radial-gradient(ellipse at 30% 20%, rgba(255,255,255,0.9), rgba(235,236,232,0.6) 70%, rgba(220,222,216,0.7))' }}
      />
      {/* multiply no grupo de fora: o branco da imagem vira o branco da lousa (o clip-path isola o que está dentro) */}
      <AbsoluteFill
        style={{ transform: zoom.transform, transformOrigin: origin, mixBlendMode: 'multiply' }}
      >
        <AbsoluteFill style={{ clipPath: `path('${d}')` }}>
          <Img src={url} style={{ width: '100%', height: '100%', objectFit: 'cover', objectPosition: origin, filter }} />
        </AbsoluteFill>
      </AbsoluteFill>
      {away < 1 ? (
        <div style={{ position: 'absolute', inset: 0, transform: `translate(${away * width * 0.5}px, ${away * height * 0.4}px)` }}>
          <Marker x={pen[0]} y={pen[1] + bob} size={unit * 0.2} tilt={-4 + Math.sin(frame * 0.4) * 3} />
        </div>
      ) : null}
    </AbsoluteFill>
  );
};

// ---------------------------------------------------------------------------------------------
// Colagem

/** Pontos (em %) de um retângulo com a borda rasgada: `inset` encolhe, `jag` é a amplitude do rasgo. */
export const tornPolygon = (seed: string, inset: number, jag: number, perSide = 16) => {
  const pts: string[] = [];
  const j = (i: number) => (random(`${seed}-${i}`) - 0.5) * 2 * jag;
  const lo = inset;
  const hi = 100 - inset;
  let i = 0;
  for (let k = 0; k < perSide; k++) pts.push(`${(lo + ((hi - lo) * k) / perSide).toFixed(2)}% ${(lo + Math.abs(j(i++))).toFixed(2)}%`);
  for (let k = 0; k < perSide; k++) pts.push(`${(hi - Math.abs(j(i++))).toFixed(2)}% ${(lo + ((hi - lo) * k) / perSide).toFixed(2)}%`);
  for (let k = 0; k < perSide; k++) pts.push(`${(hi - ((hi - lo) * k) / perSide).toFixed(2)}% ${(hi - Math.abs(j(i++))).toFixed(2)}%`);
  for (let k = 0; k < perSide; k++) pts.push(`${(lo + Math.abs(j(i++))).toFixed(2)}% ${(hi - ((hi - lo) * k) / perSide).toFixed(2)}%`);
  return `polygon(${pts.join(', ')})`;
};

/** Pose do stop motion: o quadro "segura" a mesma pose por fps/8 quadros. */
export const stopMotionStep = (frame: number, fps: number) => Math.floor(frame / Math.max(1, Math.round(fps / 8)));

const PAPER = '#D8C9AA';
const PAPER_TEXTURE = [
  'radial-gradient(circle at 20% 30%, rgba(255,255,255,0.18) 0 2px, transparent 3px)',
  'radial-gradient(circle at 70% 60%, rgba(80,50,20,0.10) 0 1px, transparent 2px)',
  'repeating-linear-gradient(17deg, rgba(120,90,50,0.05) 0 2px, transparent 2px 7px)',
  'repeating-linear-gradient(-63deg, rgba(255,255,255,0.05) 0 1px, transparent 1px 5px)',
  'radial-gradient(ellipse at 50% 40%, rgba(255,255,255,0.25), rgba(60,40,15,0.25))',
].join(', ');

/** Fita adesiva semitransparente com as pontas recortadas. */
const Tape: React.FC<{ x: number; y: number; w: number; h: number; angle: number; seed: string }> = ({ x, y, w, h, angle, seed }) => (
  <div
    style={{
      position: 'absolute',
      left: x - w / 2,
      top: y - h / 2,
      width: w,
      height: h,
      transform: `rotate(${angle}deg)`,
      backgroundColor: 'rgba(250, 244, 222, 0.62)',
      boxShadow: '0 1px 3px rgba(0,0,0,0.15)',
      clipPath: `polygon(0% ${5 + random(`${seed}-a`) * 20}%, 100% 0%, ${96 + random(`${seed}-b`) * 4}% 100%, 0% 100%)`,
    }}
  />
);

/**
 * Tamanho do recorte: largo no vertical, alto no horizontal, e nunca mais largo que 86% do quadro (no 1:1 a conta do
 * horizontal passaria da tela, e a borda rasgada e a fita sairiam cortadas).
 */
export const collageSize = (width: number, height: number) => {
  if (height > width) {
    const w = width * 0.84;
    return { w, h: Math.min(height * 0.62, w * 1.2) };
  }
  const w = Math.min(width * 0.86, height * 0.78 * 1.3);
  return { w, h: Math.min(height * 0.78, w / 1.3) };
};

const Collage: React.FC<TreatmentProps> = ({ scene, effects, zoomStrength, url, cards }) => {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const p = useProgress(scene);
  const step = stopMotionStep(frame, fps);
  const held = step * Math.max(1, Math.round(fps / 8)); // quadro "segurado" da pose
  const seed = `${layoutSeed(scene)}-collage`;
  const cont = typeof scene.treatment_continue === 'number';
  const vertical = height > width;
  const { w, h } = collageSize(width, height);
  const baseAngle = (random(`${seed}-ang`) > 0.5 ? 1 : -1) * (1.5 + random(`${seed}-angm`) * 2.5);
  const offX = (random(`${seed}-ox`) - 0.5) * width * 0.04;
  const offY = (vertical ? -height * 0.04 : 0) + (random(`${seed}-oy`) - 0.5) * height * 0.03;
  // Entrada: cai sobre a mesa, em poses (sem interpolar entre elas).
  // Na continuação o recorte já está na mesa.
  const drop = cont ? 1 : spring({ frame: held, fps, config: { damping: 13, stiffness: 140, mass: 0.7 } });
  const lift = 1 - drop;
  // Tremor da pose: pequeno, para a imagem continuar legível.
  const jx = (random(`${seed}-jx-${step}`) - 0.5) * width * 0.004;
  const jy = (random(`${seed}-jy-${step}`) - 0.5) * height * 0.003;
  const ja = (random(`${seed}-ja-${step}`) - 0.5) * 0.7;
  const angle = baseAngle + ja + lift * 6;
  const scale = 1 + lift * 0.12;
  const border = Math.min(w, h) * 0.035;
  const filter = imageFilter(effects, scene.look);
  const [fx, fy] = focusXY(scene.focus);
  const inner = keptZoom(scene, innerScale(scene.motion, zoomStrength * 0.6, p), [fx, fy]);
  // Papel colorido rasgado atrás do recorte, na cor de destaque do estilo.
  const backAngle = -baseAngle * 1.8;
  const unit = Math.min(width, height);
  return (
    <AbsoluteFill style={{ backgroundColor: PAPER, backgroundImage: PAPER_TEXTURE, overflow: 'hidden' }}>
      <div
        style={{
          position: 'absolute',
          left: width / 2 - w * 0.55 + offX * -1.5,
          top: height / 2 - h * 0.45 + offY,
          width: w * 1.05,
          height: h * 0.95,
          backgroundColor: cards.accent,
          opacity: 0.85,
          transform: `rotate(${backAngle}deg) translate(${width * 0.03}px, ${height * 0.02}px)`,
          clipPath: tornPolygon(`${seed}-back`, 2, 2.5, 12),
          filter: 'saturate(0.7)',
        }}
      />
      <div
        style={{
          position: 'absolute',
          left: width / 2 - w / 2 + offX + jx,
          top: height / 2 - h / 2 + offY + jy - lift * height * 0.05,
          width: w,
          height: h,
          transform: `rotate(${angle}deg) scale(${scale})`,
          filter: `drop-shadow(${unit * 0.008}px ${unit * (0.014 + lift * 0.03)}px ${unit * (0.012 + lift * 0.02)}px rgba(30,20,10,${0.45 - lift * 0.15}))`,
        }}
      >
        {/* borda de papel branco rasgada */}
        <div style={{ position: 'absolute', inset: 0, backgroundColor: '#F7F2E7', clipPath: tornPolygon(`${seed}-edge`, 0, 1.4) }} />
        {/* imagem recortada, com o mesmo rasgo um pouco para dentro */}
        <div style={{ position: 'absolute', inset: border, overflow: 'hidden', clipPath: tornPolygon(`${seed}-cut`, 0, 0.8) }}>
          <Img
            src={url}
            style={{
              width: '100%',
              height: '100%',
              objectFit: 'cover',
              objectPosition: `${fx * 100}% ${fy * 100}%`,
              transform: inner.transform,
              transformOrigin: `${fx * 100}% ${fy * 100}%`,
              filter: filter || undefined,
            }}
          />
          {/* textura de papel por cima da foto (leve) */}
          <AbsoluteFill style={{ backgroundImage: PAPER_TEXTURE, mixBlendMode: 'multiply', opacity: 0.35 }} />
        </div>
        <Tape x={w * 0.12} y={h * 0.02} w={w * 0.26} h={unit * 0.05} angle={-38} seed={`${seed}-t1`} />
        <Tape x={w * 0.9} y={h * 0.98} w={w * 0.24} h={unit * 0.05} angle={-32} seed={`${seed}-t2`} />
      </div>
    </AbsoluteFill>
  );
};

// ---------------------------------------------------------------------------------------------
// Interface de tela

export type ScreenVariant = 'window' | 'phone' | 'feed';

/**
 * Moldura da cena: no vertical, celular ou feed (às vezes janela); no horizontal, janela na maioria. Captura de página
 * (`page`) é sempre janela: ela já vem na proporção do vídeo, e o `cover` no celular mostrava só uma faixa estreita do
 * meio dela (a manchete cortada dos dois lados, ou só o branco do topo da página).
 */
export const screenVariant = (seed: number, width: number, height: number, page = false): ScreenVariant => {
  if (page) return 'window';
  const pool: ScreenVariant[] = height > width ? ['phone', 'feed', 'phone', 'feed', 'window'] : ['window', 'window', 'phone', 'feed'];
  return pool[Math.floor(random(`${seed}-screen`) * pool.length) % pool.length];
};

/** A moldura que a cena usa de fato (a mesma semente do `Screen`). */
export const sceneScreenVariant = (scene: TimelineScene, width: number, height: number) =>
  screenVariant(layoutSeed(scene), width, height, scene.prov === 'capture');

type Rect = PxRect;

/**
 * Onde a manchete guardada (`scene.keep`) cai na tela quando a cena é a captura dentro da janela (`screen`, sempre
 * janela para captura): a área da imagem da janela, já com o zoom de dentro (`keptZoom`). Null fora disso. A câmera
 * por eventos usa isto para também não cortá-la.
 */
export const treatmentKeep = (scene: TimelineScene, treatment: SceneTreatment, width: number, height: number, zoomStrength: number, frame: number) => {
  const keep = pRects({ rect: scene.keep })[0];
  if (!keep || treatment !== 'screen' || screenVariant(layoutSeed(scene), width, height, scene.prov === 'capture') !== 'window') return null;
  const { content } = screenFrame('window', width, height);
  const dur = Math.max(1, scene.durationInFrames - 1);
  const p = interpolate(frame, [0, dur], [0, 1], { ...clamp, easing: Easing.inOut(Easing.quad) });
  const clickZoom = interpolate(frame, [dur * 0.55, dur * 0.8], [0, 1], { ...clamp, easing: Easing.out(Easing.cubic) });
  const [fx, fy] = focusXY(scene.focus);
  const zoom = keptZoom(scene, innerScale(scene.motion, zoomStrength * 0.6, p) * (1 + clickZoom * 0.12), [fx, fy]);
  const r = rectThrough(keep, zoom.scale, [fx, fy], zoom.t);
  return [
    (content.x + r[0] * content.w) / width,
    (content.y + r[1] * content.h) / height,
    (r[2] * content.w) / width,
    (r[3] * content.h) / height,
  ] as [number, number, number, number];
};

/** Linha cinza no lugar de texto (a interface nunca tem texto legível). */
const Bar: React.FC<{ w: number | string; h: number; color?: string; style?: React.CSSProperties }> = ({ w, h, color = 'rgba(255,255,255,0.22)', style }) => (
  <div style={{ width: w, height: h, borderRadius: h / 2, backgroundColor: color, flexShrink: 0, ...style }} />
);

const Cursor: React.FC<{ x: number; y: number; size: number; press: number }> = ({ x, y, size, press }) => (
  <svg
    width={size}
    height={size * 1.4}
    viewBox="0 0 20 28"
    style={{ position: 'absolute', left: x, top: y, transform: `scale(${1 - press * 0.15})`, transformOrigin: '0 0', filter: 'drop-shadow(0 3px 4px rgba(0,0,0,0.45))' }}
  >
    <path d="M1 1 L1 22 L6.5 17 L10.5 26 L14 24.5 L10 15.8 L17.5 15.5 Z" fill="#fff" stroke="#111" strokeWidth="1.6" strokeLinejoin="round" />
  </svg>
);

const Heart: React.FC<{ size: number; filled: number }> = ({ size, filled }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" style={{ transform: `scale(${1 + Math.sin(Math.PI * Math.min(1, filled)) * 0.3})` }}>
    <path
      d="M12 21s-7.5-4.6-9.6-9.2C.9 8.4 3 4.5 6.7 4.5c2.1 0 3.6 1.2 5.3 3 1.7-1.8 3.2-3 5.3-3 3.7 0 5.8 3.9 4.3 7.3C19.5 16.4 12 21 12 21z"
      fill={filled > 0.5 ? '#EF4444' : 'none'}
      stroke={filled > 0.5 ? '#EF4444' : 'rgba(255,255,255,0.85)'}
      strokeWidth="2"
    />
  </svg>
);

const IconCircle: React.FC<{ size: number }> = ({ size }) => (
  <svg width={size} height={size} viewBox="0 0 24 24">
    <path d="M4 12a8 7 0 1 1 3 5.5L4 20l1-3.7A7 7 0 0 1 4 12z" fill="none" stroke="rgba(255,255,255,0.85)" strokeWidth="2" strokeLinejoin="round" />
  </svg>
);

const IconSend: React.FC<{ size: number }> = ({ size }) => (
  <svg width={size} height={size} viewBox="0 0 24 24">
    <path d="M3 11 21 3l-7 18-3-7z" fill="none" stroke="rgba(255,255,255,0.85)" strokeWidth="2" strokeLinejoin="round" />
  </svg>
);

/**
 * Posição do cursor (px) e o "aperto" do clique: entra pelo canto aos 15% da cena, chega no alvo aos 50% e clica
 * aos 55%; depois fica parado perto do alvo.
 */
export const cursorPath = (frame: number, dur: number, from: [number, number], to: [number, number]) => {
  const tIn = dur * 0.15;
  const tArrive = dur * 0.5;
  const tClick = dur * 0.55;
  const q = interpolate(frame, [tIn, tArrive], [0, 1], { ...clamp, easing: Easing.inOut(Easing.cubic) });
  // Curva suave (não reta): o meio do caminho desvia um pouco para cima.
  const bend = Math.sin(Math.PI * q) * 0.12;
  const x = from[0] + (to[0] - from[0]) * q;
  const y = from[1] + (to[1] - from[1]) * q - bend * Math.abs(to[0] - from[0]);
  const press = interpolate(frame, [tClick - 3, tClick, tClick + 4], [0, 1, 0], clamp);
  const ripple = interpolate(frame, [tClick, tClick + 14], [0, 1], clamp);
  return { x, y, press, ripple, clicked: frame >= tClick, visible: frame >= tIn - 1 };
};

const Screen: React.FC<TreatmentProps> = ({ scene, effects, zoomStrength, url, cards }) => {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const p = useProgress(scene);
  const unit = Math.min(width, height);
  const vertical = height > width;
  const cont = typeof scene.treatment_continue === 'number';
  const variant = screenVariant(layoutSeed(scene), width, height, scene.prov === 'capture');
  const filter = imageFilter(effects, scene.look);
  // Na continuação o aparelho já está em cena.
  const enter = cont ? 1 : spring({ frame, fps, config: { damping: 16, stiffness: 120, mass: 0.8 } });
  const [fx, fy] = focusXY(scene.focus);

  // Moldura do aparelho, retângulo do conteúdo e área da imagem (no feed, o post fica entre o cabeçalho e as ações):
  // a mesma conta que leva as letras da imagem para a tela (`screenFrame`, para o carimbo desviar delas).
  const { device, content, media } = screenFrame(variant, width, height);
  const radius = variant === 'window' ? unit * 0.022 : device.w * 0.13;
  const contentRadius = variant === 'window' ? 0 : device.w * 0.1;

  // Cursor: aponta o foco da imagem (no feed, vai até o coração e curte).
  const dur = Math.max(1, scene.durationInFrames - 1);
  const icon = unit * (variant === 'feed' ? (vertical ? 0.07 : 0.05) : 0.05);
  const actionsY = media.y + media.h + content.h * 0.025;
  const heartAt: [number, number] = [media.x + content.w * 0.06 + icon * 0.5, actionsY + icon * 0.5];
  const target: [number, number] = variant === 'feed' ? heartAt : [media.x + media.w * fx, media.y + media.h * fy];
  // Na continuação o cursor já está na tela, perto do alvo, e só anda até ele.
  const start: [number, number] = cont ? [target[0] + unit * 0.12, target[1] + unit * 0.09] : [width * 1.04, height * (vertical ? 0.86 : 0.95)];
  const path = cursorPath(frame, dur, start, target);
  const cur = cont ? { ...path, visible: true } : path;
  // Depois do clique na janela/celular, a imagem aproxima do ponto clicado.
  const clickZoom = variant === 'feed' ? 0 : interpolate(frame, [dur * 0.55, dur * 0.8], [0, 1], { ...clamp, easing: Easing.out(Easing.cubic) });
  const inner = keptZoom(scene, innerScale(scene.motion, zoomStrength * 0.6, p) * (1 + clickZoom * 0.12), [fx, fy]);

  const deviceStyle: React.CSSProperties = {
    position: 'absolute',
    left: device.x,
    top: device.y,
    width: device.w,
    height: device.h,
    borderRadius: radius,
    // Opaco desde o 1º quadro: só sobe e cresce. Com fade, os primeiros quadros ficavam só no fundo desfocado (quadro
    // morto, sem nada nítido, ainda mais com a transição de desfoque por cima).
    transform: `translateY(${(1 - enter) * unit * 0.06}px) scale(${0.94 + 0.06 * enter})`,
    boxShadow: `0 ${unit * 0.03}px ${unit * 0.08}px rgba(0,0,0,0.55)`,
    overflow: 'hidden',
  };
  // Coordenadas do conteúdo relativas ao aparelho.
  const rel = (r: Rect): React.CSSProperties => ({ position: 'absolute', left: r.x - device.x, top: r.y - device.y, width: r.w, height: r.h });
  const image = (
    <Img
      src={url}
      style={{
        width: '100%',
        height: '100%',
        objectFit: 'cover',
        // O recorte (celular/feed num vídeo horizontal mostra uma faixa estreita da imagem) fica no foco da cena: com
        // `objectPosition` igual ao foco, o ponto do foco cai em `fx`/`fy` do conteúdo, onde o cursor aponta.
        objectPosition: `${fx * 100}% ${fy * 100}%`,
        transform: inner.transform,
        transformOrigin: `${fx * 100}% ${fy * 100}%`,
        filter: filter || undefined,
      }}
    />
  );

  return (
    <AbsoluteFill style={{ backgroundColor: cards.background, overflow: 'hidden' }}>
      {/* fundo: a própria imagem desfocada e escura, com um brilho na cor de destaque */}
      <AbsoluteFill style={{ transform: 'scale(1.25)' }}>
        <Img src={url} style={{ width: '100%', height: '100%', objectFit: 'cover', filter: `blur(${unit * 0.04}px) brightness(0.4) saturate(1.2)` }} />
      </AbsoluteFill>
      <AbsoluteFill style={{ background: `radial-gradient(ellipse at 50% 35%, transparent 20%, ${cards.background} 95%)`, opacity: 0.8 }} />

      <div style={{ ...deviceStyle, backgroundColor: variant === 'window' ? '#1B1F27' : '#0A0A0C' }}>
        {variant === 'window' ? (
          <>
            {/* barra da janela: três botões e a barra de endereço (sem texto) */}
            <div
              style={{
                position: 'absolute',
                left: 0,
                top: 0,
                width: device.w,
                height: content.y - device.y,
                display: 'flex',
                alignItems: 'center',
                gap: unit * 0.012,
                padding: `0 ${unit * 0.022}px`,
                backgroundColor: '#262B35',
                boxSizing: 'border-box',
              }}
            >
              {['#FF5F57', '#FEBC2E', '#28C840'].map((c) => (
                <div key={c} style={{ width: unit * 0.018, height: unit * 0.018, borderRadius: '50%', backgroundColor: c, flexShrink: 0 }} />
              ))}
              <div
                style={{
                  marginLeft: unit * 0.03,
                  flex: 1,
                  maxWidth: device.w * 0.55,
                  height: unit * 0.032,
                  borderRadius: unit * 0.016,
                  backgroundColor: 'rgba(255,255,255,0.08)',
                  display: 'flex',
                  alignItems: 'center',
                  gap: unit * 0.01,
                  padding: `0 ${unit * 0.014}px`,
                  boxSizing: 'border-box',
                }}
              >
                <div style={{ width: unit * 0.012, height: unit * 0.012, borderRadius: '30%', backgroundColor: 'rgba(255,255,255,0.4)', flexShrink: 0 }} />
                <Bar w="45%" h={unit * 0.01} />
              </div>
            </div>
            <div style={{ ...rel(content), overflow: 'hidden' }}>{image}</div>
          </>
        ) : (
          <div style={{ ...rel(content), borderRadius: contentRadius, overflow: 'hidden', backgroundColor: '#111318' }}>
            {variant === 'phone' ? (
              image
            ) : (
              <>
                {/* cabeçalho do post: avatar e duas linhas */}
                <div
                  style={{
                    position: 'absolute',
                    left: 0,
                    top: content.h * 0.045,
                    width: content.w,
                    height: content.h * 0.1,
                    display: 'flex',
                    alignItems: 'center',
                    gap: content.w * 0.035,
                    padding: `0 ${content.w * 0.05}px`,
                    boxSizing: 'border-box',
                  }}
                >
                  <div
                    style={{ width: content.h * 0.06, height: content.h * 0.06, borderRadius: '50%', flexShrink: 0, background: `linear-gradient(135deg, ${cards.accent}, #F472B6)` }}
                  />
                  <div style={{ display: 'flex', flexDirection: 'column', gap: content.h * 0.012 }}>
                    <Bar w={content.w * 0.32} h={content.h * 0.016} color="rgba(255,255,255,0.55)" />
                    <Bar w={content.w * 0.2} h={content.h * 0.012} />
                  </div>
                </div>
                <div style={{ position: 'absolute', left: 0, top: media.y - content.y, width: media.w, height: media.h, overflow: 'hidden' }}>{image}</div>
                {/* ações e legenda do post (blocos) */}
                <div
                  style={{
                    position: 'absolute',
                    left: content.w * 0.06,
                    top: actionsY - content.y,
                    display: 'flex',
                    gap: icon * 0.55,
                    alignItems: 'center',
                  }}
                >
                  <Heart size={icon} filled={cur.clicked ? interpolate(frame, [dur * 0.55, dur * 0.55 + 8], [0.6, 1], clamp) : 0} />
                  <IconCircle size={icon} />
                  <IconSend size={icon} />
                </div>
                <div
                  style={{
                    position: 'absolute',
                    left: content.w * 0.06,
                    top: actionsY - content.y + icon * 1.5,
                    display: 'flex',
                    flexDirection: 'column',
                    gap: content.h * 0.014,
                  }}
                >
                  <Bar w={content.w * 0.3} h={content.h * 0.016} color="rgba(255,255,255,0.5)" />
                  <Bar w={content.w * 0.78} h={content.h * 0.013} />
                  <Bar w={content.w * 0.55} h={content.h * 0.013} />
                </div>
              </>
            )}
          </div>
        )}
        {variant !== 'window' ? (
          // ilha da câmera do celular
          <div
            style={{
              position: 'absolute',
              left: device.w * 0.36,
              top: device.w * 0.06,
              width: device.w * 0.28,
              height: device.w * 0.075,
              borderRadius: device.w * 0.04,
              backgroundColor: '#000',
            }}
          />
        ) : null}
      </div>

      {cur.visible ? (
        <>
          {cur.ripple > 0 && cur.ripple < 1 ? (
            <div
              style={{
                position: 'absolute',
                left: target[0] - unit * 0.05 * cur.ripple,
                top: target[1] - unit * 0.05 * cur.ripple,
                width: unit * 0.1 * cur.ripple,
                height: unit * 0.1 * cur.ripple,
                borderRadius: '50%',
                border: `${unit * 0.005}px solid ${cards.accent}`,
                opacity: 1 - cur.ripple,
              }}
            />
          ) : null}
          <Cursor x={cur.x} y={cur.y} size={unit * 0.045} press={cur.press} />
        </>
      ) : null}
    </AbsoluteFill>
  );
};

// ---------------------------------------------------------------------------------------------

const VIEWS: Record<SceneTreatment, React.FC<TreatmentProps>> = { whiteboard: Whiteboard, collage: Collage, screen: Screen };

/**
 * Entrada que mexe no quadro (a mesma do VisualLayer): `zoom_blur` aproxima e desfoca nos primeiros quadros, `glitch`
 * treme; o efeito `vhs` treme sempre. Sem isso a transição sorteada viraria corte seco nas cenas com tratamento.
 */
export const treatmentEntry = (transition: string, frame: number, transitionFrames: number, seed: number, effects: EffectSpec[]) => {
  const tf = Math.max(1, transitionFrames);
  let scale = 1;
  let blur = 0;
  if (transition === 'zoom_blur' && frame < tf) {
    const k = 1 - frame / tf;
    scale = 1 + 0.14 * k;
    blur = 10 * k;
  }
  let jitter = 0;
  if (transition === 'glitch' && frame < tf) jitter = (random(`${seed}-g-${frame}`) - 0.5) * 60;
  const vhs = hasEffect(effects, 'vhs');
  if (vhs) jitter += (random(`${seed}-v-${frame}`) - 0.5) * 6 * vhs.amount;
  return { scale, blur, jitter };
};

/** Fundo da cena de imagem desenhado pelo tratamento (whiteboard, colagem ou tela). */
export const TreatmentLayer: React.FC<TreatmentProps & { treatment: SceneTreatment }> = ({ treatment, ...props }) => {
  const frame = useCurrentFrame();
  const View = VIEWS[treatment];
  const { scale, blur, jitter } = treatmentEntry(props.scene.transition, frame, props.transitionFrames, props.scene.seed, props.effects);
  if (scale === 1 && blur === 0 && jitter === 0) return <View {...props} />;
  return (
    <AbsoluteFill style={{ transform: `translateX(${jitter}px) scale(${scale})`, filter: blur ? `blur(${blur}px)` : undefined }}>
      <View {...props} />
    </AbsoluteFill>
  );
};
