import React, { useState } from 'react';
import { Img, interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { fontStack } from '../fonts';
import type { TextCardStyle, TimelineNews } from '../types';
import { type CardBand, charEm } from './DataCards';

// Mídia real: chip de crédito (qualquer cena que mostra mídia real) e cartão de notícia (cena de imagem dona da
// notícia). O `src` da cena já vem enquadrado pelo backend (cover com foco ou contain com fundo desfocado), então a
// foto em si passa pelo `VisualLayer` como qualquer imagem.

/** Endereço de um arquivo do diretório de dados (imagem da cena, miniatura da notícia); URL completa passa direto. */
export const assetUrl = (src: string | null | undefined, base: string | null | undefined) => {
  if (!src) return null;
  if (/^(https?:|data:|blob:)/.test(src)) return src;
  const clean = src.replace(/\\/g, '/').replace(/^\/+/, '');
  return `${(base ?? '/files/').replace(/\/?$/, '/')}${clean}`;
};

const clamp = { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' } as const;

// ---------------------------------------------------------------------------------------------
// Chip de crédito

export type CreditCorner = 'top_left' | 'bottom_right';

/** Letra do chip (px): pequena, mas legível no celular. */
const chipFont = (width: number, height: number) => Math.max(13, Math.min(width, height) * 0.021);
const CHIP_PAD_Y = 0.28;
const CHIP_PAD_X = 0.55;
const CHIP_LINE = 1.2;

/** Meia altura do chip em fração da altura da tela (o painel e a legenda desviam dele). */
export const creditChipHalf = (width: number, height: number) => (chipFont(width, height) * (CHIP_LINE + CHIP_PAD_Y * 2)) / 2 / height;

/** Faixa de cima do vertical que o TikTok/Reels cobrem com a própria interface (e onde ficam logos da arte). */
const TOP_UI = 0.13;

/**
 * Lugar do chip: no horizontal, canto de baixo à direita (do lado oposto à fonte, que fica à esquerda); no vertical,
 * canto de cima à esquerda, logo abaixo da faixa do topo (`headBottom`, fração da altura) e sempre abaixo dos 13% de
 * cima (`TOP_UI`): longe da coluna de botões da direita e dos 20% de baixo (nome do canal e descrição).
 */
export const creditChipPlace = (width: number, height: number, headBottom?: number): { corner: CreditCorner; y: number } =>
  width > height
    ? { corner: 'bottom_right', y: 0.925 }
    : { corner: 'top_left', y: Math.max(TOP_UI + creditChipHalf(width, height) + 0.01, headBottom !== undefined ? headBottom + 0.035 : 0) };

/** Até quantas letras o fim do crédito (origem e licença) fica sempre inteiro. */
const CREDIT_TAIL_MAX = 40;

/**
 * Crédito em começo e fim: só o começo (prefixo e autor) encolhe com reticências; o fim fica inteiro — a licença
 * (" · CC BY-SA 4.0") e, se couber em `CREDIT_TAIL_MAX`, a origem antes dela (" / Wikimedia Commons"). Sem separador,
 * o texto todo encolhe.
 */
const creditParts = (label: string): [string, string] => {
  const dot = label.lastIndexOf(' · ');
  const slash = label.lastIndexOf(' / ', dot > 0 ? dot : undefined);
  const cut = slash > 0 && label.length - slash <= CREDIT_TAIL_MAX ? slash : dot;
  return cut > 0 ? [label.slice(0, cut), label.slice(cut)] : [label, ''];
};

/** "Foto: Fulano / Wikimedia Commons · CC BY-SA 4.0" pequeno, num canto; entra nos quadros 6–14 e fica a cena toda. */
export const CreditChip: React.FC<{
  text: string;
  corner: CreditCorner;
  /** Centro do chip em fração da altura. */
  y: number;
  /** Largura máxima (px); sem ela, 62% da largura da tela. Texto maior encolhe o autor com reticências; origem e
   * licença ficam inteiras. */
  maxWidth?: number;
}> = ({ text, corner, y, maxWidth }) => {
  const frame = useCurrentFrame();
  const { width, height } = useVideoConfig();
  const label = text.trim();
  if (!label) return null;
  const size = chipFont(width, height);
  const [head, tail] = creditParts(label);
  const side: React.CSSProperties = corner === 'top_left' ? { left: width * 0.06 } : { right: width * 0.04 };
  return (
    <div
      style={{
        position: 'absolute',
        ...side,
        top: height * y,
        transform: 'translateY(-50%)',
        maxWidth: maxWidth ?? width * 0.62,
        padding: `${size * CHIP_PAD_Y}px ${size * CHIP_PAD_X}px`,
        borderRadius: size * 0.35,
        backgroundColor: 'rgba(0,0,0,0.55)',
        color: '#FFFFFF',
        fontFamily: fontStack('Noto Sans'),
        fontWeight: 600,
        fontSize: size,
        lineHeight: CHIP_LINE,
        letterSpacing: '0.01em',
        textShadow: '0 1px 2px rgba(0,0,0,0.45)',
        display: 'flex',
        whiteSpace: 'nowrap',
        overflow: 'hidden',
        opacity: interpolate(frame, [6, 14], [0, 1], clamp),
      }}
    >
      <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>{head}</span>
      {tail ? (
        // `pre` guarda o espaço do começo; `maxWidth` é a rede de segurança se o fim for mais largo que o chip.
        <span style={{ flexShrink: 0, maxWidth: '100%', whiteSpace: 'pre', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {tail}
        </span>
      ) : null}
    </div>
  );
};

// ---------------------------------------------------------------------------------------------
// Cartão de notícia

const PAPER = '#F4F2EC';
const INK = '#111111';
const MUTED = '#555555';

/** Largura média de letra da manchete (Noto Sans 700, maiúsculas e minúsculas), em em: `charEm` mede maiúsculas. */
const HEAD_EM = charEm('Noto Sans') * 0.86;
const HEAD_LINES = 4;
const HEAD_LINE_HEIGHT = 1.18;
/** Miniatura: 16:9 quando cabe; na faixa baixa (horizontal) pode alargar até 2,4:1 antes de o cartão encolher. */
const THUMB_RATIO = 16 / 9;
const THUMB_RATIO_MAX = 2.4;

const QUOTES = /^[\s"'“”‘’«»„]+|[\s"'“”‘’«»„]+$/gu;

const upper = (text: string, lang: string) => {
  try {
    return text.toLocaleUpperCase(lang);
  } catch {
    return text.toUpperCase();
  }
};

/** Tamanho da manchete para caber em até `lines` linhas, quebrando nas palavras (estimativa conservadora). */
const headlineSize = (text: string, maxWidth: number, maxSize: number, minSize: number, lines: number) => {
  const longest = text.split(/\s+/).reduce((m, w) => Math.max(m, w.length), 1);
  const perLine = Math.max(longest, Math.ceil((text.length * 1.12) / lines));
  return Math.max(minSize, Math.min(maxSize, maxWidth / (perLine * HEAD_EM)));
};

const headlineLines = (text: string, size: number, maxWidth: number) =>
  Math.max(1, Math.min(HEAD_LINES, Math.ceil((text.length * HEAD_EM * size * 1.08) / Math.max(1, maxWidth))));

/**
 * Cartão de notícia desenhado pelo app (nunca captura de tela): papel claro com barra na cor de destaque, veículo e
 * data, miniatura da imagem da notícia (quando há) e a manchete — entre aspas só quando é a do veículo, literal
 * (`quoted`); traduzida ou editada, sem aspas. Fica no meio da faixa livre (`band`,
 * entre a faixa do topo e a legenda) e encolhe se não couber. Sem veículo nem manchete, não desenha nada.
 */
export const NewsCard: React.FC<{
  news: TimelineNews;
  style: TextCardStyle;
  lang: string;
  band: CardBand;
  assetBaseUrl: string | null | undefined;
}> = ({ news, style, lang, band, assetBaseUrl }) => {
  const frame = useCurrentFrame();
  const { fps, width, height, durationInFrames } = useVideoConfig();
  // Miniatura que não carrega (arquivo sumido) sai do cartão em vez de travar o render.
  const [broken, setBroken] = useState(false);
  const outlet = upper((news.outlet ?? '').trim(), lang);
  const headline = (news.headline ?? '').replace(QUOTES, '');
  if (!outlet && !headline) return null;
  const date = (news.date ?? '').trim();
  const image = broken ? null : assetUrl(news.image, assetBaseUrl);

  const portrait = height >= width;
  const u = Math.min(width, height);
  const panelW = width * (portrait ? 0.86 : 0.52);
  const pad = u * (portrait ? 0.045 : 0.038);
  const barW = Math.max(6, u * 0.012);
  const inner = panelW - pad * 2 - barW;
  const gap = pad * 0.55;
  const metaSize = u * (portrait ? 0.03 : 0.025);
  const headText = headline && news.quoted === true ? `“${headline}”` : headline;
  const headSize = headlineSize(headText, inner, u * (portrait ? 0.064 : 0.05), u * (portrait ? 0.04 : 0.032), HEAD_LINES);
  const headH = headText ? headlineLines(headText, headSize, inner) * headSize * HEAD_LINE_HEIGHT : 0;
  const metaH = outlet || date ? metaSize * 1.25 : 0;
  const blocks = [metaH, headH].filter(Boolean).length + (image ? 1 : 0);
  const fixedH = pad * 2 + metaH + headH + gap * Math.max(0, blocks - 1);

  // Altura: a miniatura cede primeiro (até 2,4:1); se ainda não couber na faixa, o cartão inteiro encolhe.
  const room = Math.max(1, (band.bottom - band.top) * height);
  const thumbH = image ? Math.max(inner / THUMB_RATIO_MAX, Math.min(inner / THUMB_RATIO, room - fixedH)) : 0;
  const panelH = fixedH + thumbH;
  const fit = Math.min(1, room / panelH);
  const center = ((band.top + band.bottom) / 2) * height;

  const s = spring({ frame: frame - 2, fps, config: { damping: 18, stiffness: 150, mass: 0.7 }, durationInFrames: 12 });
  const rise = interpolate(s, [0, 1], [height * 0.05, 0]);
  const zoom = interpolate(frame, [0, Math.max(1, durationInFrames - 1)], [1, 1.04], clamp);
  const radius = u * 0.022;

  return (
    <div
      style={{
        position: 'absolute',
        left: (width - panelW) / 2,
        width: panelW,
        top: center,
        transform: `translateY(-50%) translateY(${rise}px)${fit < 1 ? ` scale(${fit})` : ''}`,
        opacity: Math.min(1, s * 1.5),
      }}
    >
      <div
        style={{
          position: 'relative',
          overflow: 'hidden',
          borderRadius: radius,
          backgroundColor: PAPER,
          boxShadow: `0 ${u * 0.03}px ${u * 0.09}px rgba(0,0,0,0.5)`,
          padding: `${pad}px ${pad}px ${pad}px ${pad + barW}px`,
          display: 'flex',
          flexDirection: 'column',
          gap,
          color: INK,
          fontFamily: fontStack('Noto Sans'),
          textAlign: 'left',
        }}
      >
        <div style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: barW, backgroundColor: style.accent }} />
        {metaH ? (
          <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: pad * 0.5, fontSize: metaSize, lineHeight: 1.25 }}>
            {outlet ? (
              <span style={{ fontWeight: 800, letterSpacing: '0.06em', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', minWidth: 0 }}>
                {outlet}
              </span>
            ) : null}
            {date ? <span style={{ fontWeight: 500, color: MUTED, whiteSpace: 'nowrap', flexShrink: 0 }}>{date}</span> : null}
          </div>
        ) : null}
        {image ? (
          <div style={{ height: thumbH, borderRadius: u * 0.012, overflow: 'hidden', backgroundColor: '#D9D6CE', flexShrink: 0 }}>
            <Img
              src={image}
              maxRetries={1}
              onError={() => setBroken(true)}
              style={{ width: '100%', height: '100%', objectFit: 'cover', transform: `scale(${zoom})` }}
            />
          </div>
        ) : null}
        {headText ? (
          <div
            style={{
              fontWeight: 700,
              fontSize: headSize,
              lineHeight: HEAD_LINE_HEIGHT,
              textWrap: 'balance',
              display: '-webkit-box',
              WebkitBoxOrient: 'vertical',
              WebkitLineClamp: HEAD_LINES,
              overflow: 'hidden',
            }}
          >
            {headText}
          </div>
        ) : null}
      </div>
    </div>
  );
};
