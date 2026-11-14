import React from 'react';
import { interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { fontStack } from '../fonts';
import type { CaptionLine, CaptionStyle } from '../types';

type Props = { lines: CaptionLine[]; style: CaptionStyle };

const clean = (text: string, uppercase: boolean, dropPunctuation: boolean) => {
  let t = dropPunctuation ? text.replace(/[.,;:!?…"“”]+$/u, '') : text;
  if (uppercase) t = t.toLocaleUpperCase();
  return t;
};

/** Legendas queimadas: karaokê (palavra ativa destacada), palavra a palavra ou linha. */
export const Captions: React.FC<Props> = ({ lines, style }) => {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  if (style.style === 'none') return null;
  const line = lines.find((l) => frame >= l.from && frame < l.to);
  if (!line) return null;
  const scale = width / 1080;
  const fontSize = style.size * scale;
  const top = style.position === 'top' ? height * 0.16 : style.position === 'center' ? height * 0.56 : height * 0.8;
  const enter = spring({ frame: frame - line.from, fps, config: { damping: 16, stiffness: 220, mass: 0.5 } });
  const lineScale = interpolate(enter, [0, 1], [0.86, 1]);
  const stroke = style.stroke * scale;
  const baseText: React.CSSProperties = {
    fontFamily: fontStack(style.font),
    fontWeight: style.weight,
    fontSize,
    lineHeight: 1.12,
    color: style.color,
    WebkitTextStroke: stroke ? `${stroke}px ${style.stroke_color}` : undefined,
    paintOrder: 'stroke fill',
    textShadow: `0 ${4 * scale}px ${14 * scale}px rgba(0,0,0,0.55)`,
    letterSpacing: style.uppercase ? '0.01em' : undefined,
  };
  const activeIndex = line.words.findIndex((w, i) => frame >= w.from && (i === line.words.length - 1 || frame < line.words[i + 1].from));
  return (
    <div
      style={{
        position: 'absolute',
        left: width * 0.07,
        right: width * 0.07,
        top,
        transform: `translateY(-50%) scale(${lineScale})`,
        textAlign: 'center',
        display: 'flex',
        flexWrap: 'wrap',
        justifyContent: 'center',
        gap: `0 ${fontSize * 0.28}px`,
        padding: style.background ? `${fontSize * 0.2}px ${fontSize * 0.4}px` : undefined,
        backgroundColor: style.background ?? undefined,
        borderRadius: style.background ? fontSize * 0.25 : undefined,
      }}
    >
      {style.style === 'word'
        ? (() => {
            const w = line.words[Math.max(0, activeIndex)];
            const pop = spring({ frame: frame - w.from, fps, config: { damping: 12, stiffness: 260, mass: 0.4 } });
            return (
              <span style={{ ...baseText, color: style.highlight, transform: `scale(${interpolate(pop, [0, 1], [0.7, 1])})`, display: 'inline-block' }}>
                {clean(w.text, style.uppercase, true)}
              </span>
            );
          })()
        : line.words.map((w, i) => {
            const active = i === activeIndex;
            const spoken = activeIndex >= 0 && i <= activeIndex;
            const pop = active ? spring({ frame: frame - w.from, fps, config: { damping: 14, stiffness: 300, mass: 0.4 } }) : 0;
            const color = style.style === 'karaoke' ? (active ? style.highlight : style.color) : active ? style.highlight : style.color;
            return (
              <span
                key={i}
                style={{
                  ...baseText,
                  color,
                  opacity: style.style === 'line' || spoken || activeIndex < 0 ? 1 : 0.92,
                  display: 'inline-block',
                  transform: active ? `scale(${1 + 0.08 * pop})` : undefined,
                }}
              >
                {clean(w.text, style.uppercase, style.style === 'karaoke')}
              </span>
            );
          })}
    </div>
  );
};
