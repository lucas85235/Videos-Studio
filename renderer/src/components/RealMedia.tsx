import React, { useState } from 'react';
import { Img, interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { fontStack } from '../fonts';
import type { TextCardStyle, TimelineNews } from '../types';
import { type CardBand, charEm } from './DataCards';
import { ENTRY_POP_LEAD, entryOpacity } from './fx/math';

// Mídia real: cartão de notícia (cena de imagem dona da notícia). O crédito das imagens não vai para a tela: fica na
// descrição da publicação. O `src` da cena já vem enquadrado pelo backend (cover com foco ou contain com fundo
// desfocado), então a foto em si passa pelo `VisualLayer` como qualquer imagem.

/** Endereço de um arquivo do diretório de dados (imagem da cena, miniatura da notícia); URL completa passa direto. */
export const assetUrl = (src: string | null | undefined, base: string | null | undefined) => {
  if (!src) return null;
  if (/^(https?:|data:|blob:)/.test(src)) return src;
  const clean = src.replace(/\\/g, '/').replace(/^\/+/, '');
  return `${(base ?? '/files/').replace(/\/?$/, '/')}${clean}`;
};

const clamp = { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' } as const;

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

  // Opaco desde o corte (`entryOpacity`): só sobe. Com fade, o 1º quadro amostrado pegava o cartão fantasma.
  const s = spring({ frame: frame + ENTRY_POP_LEAD, fps, config: { damping: 18, stiffness: 150, mass: 0.7 }, durationInFrames: 12 });
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
        opacity: entryOpacity(frame),
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
