import React from 'react';
import { interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { fontStack } from '../fonts';
import type { CaptionLine, CaptionStyle } from '../types';
import { charEm, mix } from './DataCards';

/** `topOverride`: altura do centro da linha em fração da tela (ex.: 0.76 abaixo dos cartões). */
type Props = { lines: CaptionLine[]; style: CaptionStyle; topOverride?: number };

export const captionTop = (position: CaptionStyle['position']) => (position === 'top' ? 0.16 : position === 'center' ? 0.56 : 0.8);

// Zonas seguras do vertical (TikTok, Reels, Shorts): os 20% de baixo ficam com o nome do canal e a descrição, e a
// coluna de botões (curtir, comentar, compartilhar) ocupa a direita da tela.
/** Borda de baixo da legenda no vertical, em fração da altura. */
export const SAFE_BOTTOM = 0.79;
const SIDE = { left: 0.08, right: 0.13 };
const SIDE_WIDE = { left: 0.07, right: 0.07 };

/** Meia altura do bloco de legenda (até 2 linhas), em fração da altura da tela. */
export const captionHalfHeight = (style: CaptionStyle, width: number, height: number) =>
  style.style === 'none' ? 0 : (style.size * (width / 1080) * 1.12) / height;

/** Centro da legenda em fração da altura: a posição do estilo (ou `override`); no vertical, sem passar da zona segura de baixo. */
export const captionCenter = (style: CaptionStyle, width: number, height: number, override?: number) => {
  const base = override ?? captionTop(style.position);
  if (height <= width) return base;
  return Math.min(base, SAFE_BOTTOM - captionHalfHeight(style, width, height));
};

const clean = (text: string, uppercase: boolean, dropPunctuation: boolean) => {
  let t = dropPunctuation ? text.replace(/[.,;:!?…"“”]+$/u, '') : text;
  if (uppercase) t = t.toLocaleUpperCase();
  return t;
};

/** Legendas queimadas: karaokê (palavra ativa destacada), palavra a palavra ou linha. */
export const Captions: React.FC<Props> = ({ lines, style, topOverride }) => {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  if (style.style === 'none') return null;
  const index = lines.findIndex((l) => frame >= l.from && frame < l.to);
  const line = lines[index];
  if (!line) return null;
  const scale = width / 1080;
  const side = height > width ? SIDE : SIDE_WIDE;
  const room = width * (1 - side.left - side.right);
  const stroke = style.stroke * scale;
  // Palavra comprida não vaza para a coluna de botões; linha que quase cabe numa fileira encolhe um pouco em vez
  // de quebrar.
  const em = charEm(style.font) * (style.uppercase ? 1.04 : 0.86);
  const texts = line.words.map((w) => w.text);
  const longest = Math.max(1, ...texts.map((t) => t.length));
  const total = texts.join(' ').length;
  const base = style.size * scale;
  const wordFit = (room - stroke * 2) / (longest * em);
  const rowFit = (room - stroke * 2 * texts.length) / (total * em);
  const fontSize = Math.min(base, wordFit, rowFit >= base * 0.88 ? rowFit : base);
  const top = height * captionCenter(style, width, height, topOverride);
  // Linha que emenda na anterior (mesma frase) entra com menos "pop": linhas curtas não piscam.
  const prev = index > 0 ? lines[index - 1] : undefined;
  const chained = prev !== undefined && line.from - prev.to <= 2;
  const enter = spring({ frame: frame - line.from, fps, config: { damping: 16, stiffness: 220, mass: 0.5 } });
  const lineScale = interpolate(enter, [0, 1], [chained ? 0.95 : 0.86, 1]);
  // Karaokê: a palavra ainda não dita fica numa cor opaca mais apagada; o contorno e a sombra ficam inteiros
  // (opacidade deixaria o fundo aparecer dentro das letras).
  const dimColor = style.dim_color ?? mix(style.color, style.stroke_color, 0.16);
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
  const last = line.words.length - 1;
  return (
    <div
      style={{
        position: 'absolute',
        left: width * side.left,
        right: width * side.right,
        top,
        transform: `translateY(-50%) scale(${lineScale})`,
        textAlign: 'center',
        display: 'flex',
        flexWrap: 'wrap',
        justifyContent: 'center',
        // O espaço conta o contorno, que vaza metade para cada lado da palavra.
        gap: `0 ${fontSize * 0.3 + stroke}px`,
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
            // Karaokê: a palavra ainda não falada fica apagada; no modo linha todas ficam acesas.
            const dimmed = style.style === 'karaoke' && activeIndex >= 0 && !spoken;
            return (
              <span
                key={i}
                style={{
                  ...baseText,
                  color: active ? style.highlight : dimmed ? dimColor : style.color,
                  display: 'inline-block',
                  // O destaque sobe a palavra sem mudar a largura (escala comia o espaço vizinho).
                  transform: active ? `translateY(${-0.06 * fontSize * pop}px)` : undefined,
                }}
              >
                {/* Karaokê: só a pontuação do fim da linha sai; a vírgula do meio fica ("COM LUCIA, A"). */}
                {clean(w.text, style.uppercase, style.style === 'karaoke' && i === last)}
              </span>
            );
          })}
    </div>
  );
};
