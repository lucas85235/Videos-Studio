import React from 'react';
import { AbsoluteFill, Easing, Img, interpolate, random, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { hasEffect, imageFilter } from '../look';
import type { EffectSpec, TimelineScene } from '../types';

type Props = {
  scene: TimelineScene;
  effects: EffectSpec[];
  zoomStrength: number;
  transitionFrames: number;
  url: string | null;
  fallbackColor: string;
  dim?: number;
};

/** Imagem com movimento de câmera (Ken Burns, pan, punch zoom, drift) e gradação de cor. */
export const VisualLayer: React.FC<Props> = ({ scene, effects, zoomStrength, transitionFrames, url, fallbackColor, dim = 0 }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const dur = Math.max(1, scene.durationInFrames - 1);
  const p = interpolate(frame, [0, dur], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.inOut(Easing.quad) });
  const z = zoomStrength;
  let scale = 1 + z * 0.5;
  let tx = 0;
  let ty = 0;
  const side = (r: number) => (random(`${scene.seed}-${r}`) > 0.5 ? 1 : -1);
  switch (scene.motion) {
    case 'ken_burns_in':
      scale = interpolate(p, [0, 1], [1 + z * 0.15, 1 + z]);
      tx = side(1) * interpolate(p, [0, 1], [0, z * 12]);
      break;
    case 'ken_burns_out':
      scale = interpolate(p, [0, 1], [1 + z, 1 + z * 0.15]);
      ty = side(2) * interpolate(p, [0, 1], [z * 10, 0]);
      break;
    case 'pan_left':
      scale = 1 + z;
      tx = interpolate(p, [0, 1], [z * 30, -z * 30]);
      break;
    case 'pan_right':
      scale = 1 + z;
      tx = interpolate(p, [0, 1], [-z * 30, z * 30]);
      break;
    case 'pan_up':
      scale = 1 + z;
      ty = interpolate(p, [0, 1], [z * 30, -z * 30]);
      break;
    case 'pan_down':
      scale = 1 + z;
      ty = interpolate(p, [0, 1], [-z * 30, z * 30]);
      break;
    case 'punch_zoom': {
      const punch = spring({ frame, fps, config: { damping: 14, stiffness: 160, mass: 0.6 } });
      scale = 1 + z * 0.1 + punch * z * 0.55 + p * z * 0.2;
      break;
    }
    case 'drift': {
      const t = frame / fps;
      scale = 1 + z * 0.55;
      tx = Math.sin(t * 0.45 + scene.seed) * z * 14;
      ty = Math.cos(t * 0.37 + scene.seed * 0.5) * z * 10;
      break;
    }
    case 'static':
    default:
      scale = 1 + z * 0.1;
  }

  // Transições de entrada que mexem na imagem
  const tf = Math.max(1, transitionFrames);
  let extraFilter = '';
  if (scene.transition === 'zoom_blur' && frame < tf) {
    const k = 1 - frame / tf;
    scale *= 1 + 0.14 * k;
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

  const filter = `${imageFilter(effects, scene.look)}${extraFilter}${dim ? ` brightness(${1 - dim})` : ''}`.trim();
  const transform = `translateX(${jitter}px) scale(${scale}) translate(${tx}%, ${ty}%)`;
  const chroma = hasEffect(effects, 'chromatic');

  if (!url) {
    return (
      <AbsoluteFill
        style={{
          background: `radial-gradient(ellipse at ${30 + 40 * random(`${scene.seed}-bg`)}% 40%, ${fallbackColor}cc 0%, #000 75%)`,
          filter,
        }}
      />
    );
  }
  const imgStyle: React.CSSProperties = { width: '100%', height: '100%', objectFit: 'cover', transform, filter };
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
