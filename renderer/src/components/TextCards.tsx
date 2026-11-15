import React from 'react';
import { AbsoluteFill, Img, interpolate, random, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { fontStack } from '../fonts';
import type { CaptionWord, TextCardStyle, TimelineScene } from '../types';

/** Entrada dos cartões conforme a animação do estilo (`delay` em quadros). */
export const useEntrance = (animation: TextCardStyle['animation'], seed: number, delay = 0) => {
  const frame = useCurrentFrame() - delay;
  const { fps } = useVideoConfig();
  const s = spring({ frame, fps, config: { damping: 15, stiffness: 140, mass: 0.7 } });
  switch (animation) {
    case 'pop':
      return { transform: `scale(${interpolate(s, [0, 1], [0.6, 1])})`, opacity: s };
    case 'slide':
      return { transform: `translateX(${interpolate(s, [0, 1], [-120, 0])}px)`, opacity: s };
    case 'flicker': {
      const on = frame > 18 || random(`${seed}-fl-${frame}`) > 0.45;
      return { opacity: on ? 1 : 0.15 };
    }
    case 'typewriter':
      return { opacity: 1 };
    case 'fade_up':
    default:
      return { transform: `translateY(${interpolate(s, [0, 1], [40, 0])}px)`, opacity: s };
  }
};

const Typewriter: React.FC<{ text: string; enabled: boolean }> = ({ text, enabled }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  if (!enabled) return <>{text}</>;
  const chars = Math.floor(interpolate(frame, [0, Math.max(fps * 0.9, text.length * 1.4)], [0, text.length], { extrapolateRight: 'clamp' }));
  const caret = frame % 16 < 8 && chars < text.length ? '▌' : '';
  return (
    <>
      {text.slice(0, chars)}
      <span style={{ opacity: 0.8 }}>{caret}</span>
    </>
  );
};

type CardProps = { scene: TimelineScene; style: TextCardStyle };

/** Cartão de título/capítulo: texto grande sobre a imagem escurecida. */
export const TitleCard: React.FC<CardProps> = ({ scene, style }) => {
  const { width } = useVideoConfig();
  const entrance = useEntrance(style.animation, scene.seed);
  const frame = useCurrentFrame();
  const line = interpolate(frame, [6, 26], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  const size = width * (scene.text.length > 28 ? 0.075 : 0.1);
  return (
    <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', padding: width * 0.08 }}>
      <div style={{ ...entrance, textAlign: 'center' }}>
        <div style={{ fontFamily: fontStack(style.font), color: style.color, fontSize: size, lineHeight: 1.05, textShadow: '0 6px 30px rgba(0,0,0,0.8)' }}>
          <Typewriter text={scene.text} enabled={style.animation === 'typewriter'} />
        </div>
        <div style={{ height: Math.max(3, width * 0.004), width: `${line * 60}%`, margin: `${size * 0.25}px auto 0`, backgroundColor: style.accent }} />
      </div>
    </AbsoluteFill>
  );
};

/** Cartão de contagem (Top 10): número grande + nome do item. */
export const CountCard: React.FC<CardProps> = ({ scene, style }) => {
  const frame = useCurrentFrame();
  const { fps, width } = useVideoConfig();
  const s = spring({ frame, fps, config: { damping: 11, stiffness: 120, mass: 0.8 } });
  const label = scene.text.replace(/^#?\d+\s*[-–:.]?\s*/, '');
  return (
    <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center' }}>
      <div style={{ textAlign: 'center', transform: `scale(${interpolate(s, [0, 1], [1.6, 1])})`, opacity: s }}>
        <div style={{ fontFamily: fontStack(style.font), fontSize: width * 0.26, color: style.accent, lineHeight: 0.9, textShadow: '0 10px 40px rgba(0,0,0,0.8)' }}>
          #{scene.number ?? ''}
        </div>
        {label ? (
          <div style={{ fontFamily: fontStack(style.font), fontSize: width * 0.06, color: style.color, marginTop: width * 0.02, textShadow: '0 4px 20px rgba(0,0,0,0.8)' }}>
            {label}
          </div>
        ) : null}
      </div>
    </AbsoluteFill>
  );
};

/** Citação: aspas grandes + texto. */
export const QuoteCard: React.FC<CardProps> = ({ scene, style }) => {
  const { width } = useVideoConfig();
  const entrance = useEntrance(style.animation === 'pop' ? 'fade_up' : style.animation, scene.seed);
  return (
    <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', padding: width * 0.1 }}>
      <div style={{ ...entrance, textAlign: 'center', fontFamily: fontStack(style.font), color: style.color }}>
        <div style={{ fontSize: width * 0.22, color: style.accent, lineHeight: 0.6 }}>“</div>
        <div style={{ fontSize: width * 0.065, lineHeight: 1.2, textShadow: '0 4px 24px rgba(0,0,0,0.85)' }}>
          <Typewriter text={scene.text} enabled={style.animation === 'typewriter'} />
        </div>
      </div>
    </AbsoluteFill>
  );
};

/** Tipografia cinética: as palavras da narração aparecem grandes, no ritmo da fala. */
export const KineticText: React.FC<{ scene: TimelineScene; style: TextCardStyle; uppercase: boolean }> = ({ scene, style, uppercase }) => {
  const frame = useCurrentFrame();
  const { fps, width } = useVideoConfig();
  const words: CaptionWord[] = scene.words.length ? scene.words : scene.text.split(/\s+/).map((t, i) => ({ text: t, from: i * 8, to: i * 8 + 8 }));
  const visible = words.filter((w) => w.from <= frame);
  const shown = visible.slice(-6);
  return (
    <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', padding: width * 0.08, backgroundColor: `${style.background}d9` }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'center', gap: `${width * 0.01}px ${width * 0.025}px`, maxWidth: '92%' }}>
        {shown.map((w, i) => {
          const pop = spring({ frame: frame - w.from, fps, config: { damping: 12, stiffness: 240, mass: 0.5 } });
          const accent = w.text.replace(/[^\p{L}\p{N}]/gu, '').length >= 7 || i === shown.length - 1;
          const text = uppercase ? w.text.toLocaleUpperCase() : w.text;
          return (
            <span
              key={`${w.from}-${i}`}
              style={{
                fontFamily: fontStack(style.font),
                fontSize: width * (accent ? 0.12 : 0.095),
                lineHeight: 1,
                color: accent ? style.accent : style.color,
                transform: `translateY(${interpolate(pop, [0, 1], [30, 0])}px) scale(${interpolate(pop, [0, 1], [0.7, 1])})`,
                opacity: pop,
                display: 'inline-block',
                textShadow: '0 6px 24px rgba(0,0,0,0.6)',
              }}
            >
              {text}
            </span>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};

/** Documento: a imagem vira um papel inclinado com destaque animado (true crime, jornal). */
export const DocumentCard: React.FC<{ url: string | null; scene: TimelineScene; style: TextCardStyle; filter: string }> = ({ url, scene, style, filter }) => {
  const frame = useCurrentFrame();
  const { width, height } = useVideoConfig();
  const p = interpolate(frame, [0, Math.max(1, scene.durationInFrames)], [0, 1]);
  const rot = (random(`${scene.seed}-rot`) - 0.5) * 6;
  const hl = interpolate(frame, [10, 34], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  const w = width * 0.78;
  const h = Math.min(height * 0.78, w * 1.25);
  return (
    <AbsoluteFill style={{ backgroundColor: style.background, justifyContent: 'center', alignItems: 'center' }}>
      <div
        style={{
          width: w,
          height: h,
          transform: `rotate(${rot}deg) scale(${1 + p * 0.08})`,
          backgroundColor: '#efe6d2',
          boxShadow: '0 30px 80px rgba(0,0,0,0.6)',
          padding: w * 0.04,
          position: 'relative',
        }}
      >
        {url ? <Img src={url} style={{ width: '100%', height: '100%', objectFit: 'cover', filter: `${filter} sepia(0.35) contrast(1.05)` }} /> : null}
        <div style={{ position: 'absolute', left: '8%', top: '62%', height: h * 0.05, width: `${84 * hl}%`, backgroundColor: `${style.accent}88`, mixBlendMode: 'multiply' }} />
        {scene.text ? (
          <div style={{ position: 'absolute', left: '8%', right: '8%', bottom: '6%', fontFamily: fontStack(style.font), fontSize: w * 0.05, color: '#1a1a1a' }}>
            <Typewriter text={scene.text} enabled />
          </div>
        ) : null}
      </div>
    </AbsoluteFill>
  );
};
