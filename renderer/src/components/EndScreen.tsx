import React from 'react';
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { fontStack } from '../fonts';
import { cardWeight, type CardViewProps, upper, withAlpha } from './DataCards';
import { END_GAP, ENTRY_POP_LEAD, endScreenLayout, entryOpacity, type Rect } from './fx/math';

/** Quadros entre a entrada de uma peça da tela final e a da próxima (chamado, vídeo 1, vídeo 2, inscrever). */
const STAGGER = 3;

const box = (r: Rect, width: number, height: number): React.CSSProperties => ({
  position: 'absolute',
  left: r[0] * width,
  top: r[1] * height,
  width: r[2] * width,
  height: r[3] * height,
});

/**
 * Tela final do vídeo longo (end screen do YouTube): o material do tema segue em movimento e NÍTIDO por baixo (só uma
 * película escura para os lugares lerem), com o chamado em cima, os dois lugares dos vídeos sugeridos (16:9) e o do
 * botão de inscrever, onde o YouTube põe os elementos dele. O avatar (quando há) fica do lado (`avoid`). Tudo entra
 * opaco, só com um "pop" curto, e fica parado até o fim: o vídeo nunca termina em colagem borrada nem em quadro solto.
 */
export const EndScreenCard: React.FC<CardViewProps> = ({ scene, style, lang, avoid }) => {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const u = Math.min(width, height);
  const layout = endScreenLayout(width, height, avoid);
  const font = style.font;
  const weight = cardWeight(font);
  const title = upper((scene.end?.title ?? scene.text ?? '').trim(), lang);
  const subscribe = upper((scene.end?.subscribe ?? '').trim(), lang);
  const pop = (i: number) => spring({ frame: frame - i * STAGGER + ENTRY_POP_LEAD, fps, config: { damping: 14, stiffness: 200, mass: 0.6 } });
  const shown = (i: number) => entryOpacity(frame - i * STAGGER);
  const border = Math.max(3, u * 0.005);
  const [titleRect, sub] = [layout.title, layout.subscribe];
  const subD = sub[3] * height;
  // Respiro do anel do inscrever (pulsa devagar: o olho vai até ele).
  const pulse = 1 + 0.04 * Math.sin((frame / fps) * Math.PI * 1.6);
  return (
    <AbsoluteFill>
      {/* Película escura em degradê (sem desfoque): o material continua nítido e em movimento. */}
      <AbsoluteFill style={{ background: 'linear-gradient(180deg, rgba(0,0,0,0.62) 0%, rgba(0,0,0,0.38) 45%, rgba(0,0,0,0.6) 100%)' }} />
      {title ? (
        <div
          style={{
            ...box(titleRect, width, height),
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontFamily: fontStack(font),
            fontWeight: weight,
            fontSize: Math.min(titleRect[3] * height * 0.62, (titleRect[2] * width) / Math.max(6, title.length * 0.55)),
            color: style.color,
            lineHeight: 1,
            letterSpacing: '0.02em',
            textAlign: 'center',
            textShadow: '0 4px 18px rgba(0,0,0,0.7)',
            opacity: shown(0),
            transform: `translateY(${interpolate(pop(0), [0, 1], [u * 0.03, 0])}px)`,
          }}
        >
          {title}
        </div>
      ) : null}
      {layout.videos.map((r, i) => (
        <div
          key={i}
          style={{
            ...box(r, width, height),
            borderRadius: u * 0.016,
            border: `${border}px solid ${style.accent}`,
            backgroundColor: withAlpha(style.background, 0.45),
            boxShadow: `0 ${u * 0.012}px ${u * 0.04}px rgba(0,0,0,0.5)`,
            opacity: shown(1 + i),
            transform: `scale(${interpolate(pop(1 + i), [0, 1], [0.9, 1])})`,
          }}
        />
      ))}
      <div
        style={{
          ...box(sub, width, height),
          borderRadius: '50%',
          border: `${border * 1.4}px solid ${style.accent}`,
          backgroundColor: withAlpha(style.background, 0.45),
          opacity: shown(3),
          transform: `scale(${interpolate(pop(3), [0, 1], [0.7, 1]) * pulse})`,
        }}
      />
      {subscribe ? (
        <div
          style={{
            position: 'absolute',
            left: (sub[0] + sub[2] / 2) * width - width * 0.2,
            width: width * 0.4,
            top: sub[1] * height + subD + u * END_GAP * 0.4,
            display: 'flex',
            justifyContent: 'center',
            fontFamily: fontStack(font),
            fontWeight: weight,
            fontSize: subD * 0.26,
            color: style.color,
            whiteSpace: 'nowrap',
            textShadow: '0 3px 12px rgba(0,0,0,0.7)',
            opacity: shown(3),
            transform: `translateY(${interpolate(pop(3), [0, 1], [u * 0.02, 0])}px)`,
          }}
        >
          {subscribe}
        </div>
      ) : null}
    </AbsoluteFill>
  );
};
