import React from 'react';
import { AbsoluteFill, interpolate, random, useCurrentFrame, useVideoConfig } from 'remotion';

type Props = { type: string; frames: number; seed: number };

/**
 * Transições de ENTRADA: acontecem dentro da cena que começa, sem sobrepor a anterior.
 * Assim a duração da narração não muda e cada bloco de render é independente (README §5.4).
 */
export const TransitionIn: React.FC<Props> = ({ type, frames, seed }) => {
  const frame = useCurrentFrame();
  const { width, height } = useVideoConfig();
  const tf = Math.max(1, frames);
  if (type === 'cut' || frame >= tf * 1.5) {
    return null;
  }
  const fade = (from: number) => interpolate(frame, [0, tf], [from, 0], { extrapolateRight: 'clamp' });
  switch (type) {
    case 'flash':
      return <AbsoluteFill style={{ backgroundColor: '#fff', opacity: fade(0.9) }} />;
    case 'fade_black':
      return <AbsoluteFill style={{ backgroundColor: '#000', opacity: fade(1) }} />;
    case 'light_leak':
      return (
        <AbsoluteFill
          style={{
            background: 'radial-gradient(ellipse at 30% 40%, rgba(255,230,180,1) 0%, rgba(255,140,60,0.8) 35%, rgba(0,0,0,0) 75%)',
            mixBlendMode: 'screen',
            opacity: fade(0.95),
          }}
        />
      );
    case 'film_burn':
      return (
        <AbsoluteFill
          style={{
            background: 'radial-gradient(circle at 70% 60%, rgba(255,250,220,1) 0%, rgba(255,120,20,0.9) 25%, rgba(90,20,0,0.6) 55%, rgba(0,0,0,0) 80%)',
            mixBlendMode: 'screen',
            opacity: fade(1),
          }}
        />
      );
    case 'glitch': {
      if (frame >= tf) return null;
      const bars = 6;
      return (
        <AbsoluteFill>
          {Array.from({ length: bars }).map((_, i) => {
            const r = random(`${seed}-gb-${frame}-${i}`);
            const top = random(`${seed}-gt-${frame}-${i}`) * height;
            return (
              <div
                key={i}
                style={{
                  position: 'absolute',
                  left: 0,
                  width,
                  top,
                  height: 6 + r * 40,
                  backgroundColor: r > 0.5 ? 'rgba(0,255,255,0.35)' : 'rgba(255,0,180,0.35)',
                  transform: `translateX(${(r - 0.5) * 120}px)`,
                  mixBlendMode: 'screen',
                }}
              />
            );
          })}
        </AbsoluteFill>
      );
    }
    case 'static_burst': {
      if (frame >= tf) return null;
      const id = `static-${seed}`;
      return (
        <AbsoluteFill style={{ opacity: fade(1) }}>
          <svg width={width} height={height}>
            <filter id={id}>
              <feTurbulence type="fractalNoise" baseFrequency="1.6" numOctaves={1} seed={frame + (seed % 17)} />
              <feColorMatrix type="saturate" values="0" />
            </filter>
            <rect width="100%" height="100%" filter={`url(#${id})`} />
          </svg>
        </AbsoluteFill>
      );
    }
    default:
      return null;
  }
};
