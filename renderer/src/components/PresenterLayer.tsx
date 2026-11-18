import React from 'react';
import { Easing, Img, interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import type { CaptionWord, TimelinePresenter } from '../types';
import { stickerFilter } from '../sticker';
import type { Rect } from './fx/math';
import { assetUrl } from './RealMedia';

// Avatar do apresentador (porte do `Reacao.tsx` do canais/plantao-vice): só a pose, nunca balão, texto ou som.
//   reacao  = entra por baixo do canto, fica 1,5 a 2,5 s e sai descendo (o `core/timeline.py` já limita o `dur`);
//   explica = do gatilho até o fim da cena (máx. 8 s), com pulso leve junto das palavras da fala.
// Quadros relativos ao início da cena. Não aparece no vertical (Shorts).

const clamp = { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' } as const;
/** Quadros da saída (desce). */
const EXIT = 8;
/** Fração da largura da figura que fica para fora do quadro (o corpo encosta na borda). */
const BLEED = 0.14;
/** Fração da altura da figura abaixo da borda de baixo (o corte da cintura não aparece). */
const SINK = 0.08;
/** Respiro (fração da altura do quadro) entre a cabeça e a faixa do topo. */
const TOP_GAP = 0.02;

/** Pulso da fala (0..1) a partir das palavras da cena, como no ApresentadorEncerramento do canais. */
export const speechPulse = (words: CaptionWord[] | undefined, frame: number, fps: number) => {
  const w = words?.find((x) => frame >= x.from && frame < x.to);
  if (!w) return 0;
  return 0.25 + 0.75 * Math.abs(Math.sin(((frame - w.from) / fps) * Math.PI * 2 * 4.2));
};

// O contorno de adesivo mora em `sticker.ts` (os memes também usam) e continua exportado daqui.
export { stickerFilter };

type Props = {
  presenter: TimelinePresenter;
  /** Palavras da cena (quadros relativos à cena), para o pulso do modo `explica`. */
  words?: CaptionWord[];
  assetBaseUrl: string | null | undefined;
  /** Topo livre (fração da altura): a cabeça não sobe na faixa do topo. */
  minTop?: number;
};

/** Altura da figura em fração da tela (mesma conta do componente). */
const figureHeight = (presenter: TimelinePresenter, minTop: number | undefined) => {
  const want = Math.max(0.1, Math.min(1, presenter.height || (presenter.mode === 'explica' ? 0.48 : 0.55)));
  const room = minTop !== undefined ? (1 - Math.max(0, minTop) - TOP_GAP) / (1 - SINK) : want;
  return Math.max(0.25, Math.min(want, room));
};

/**
 * Caixa [x, y, w, h] (fração da tela) do avatar no canto de baixo, para o carimbo solto desviar dele: a figura em pé
 * tem ~0,6 da altura de largura (a proporção do arquivo não chega aqui). Null no vertical, onde ele não aparece.
 */
export const presenterRect = (presenter: TimelinePresenter, width: number, height: number, minTop?: number): Rect | null => {
  if (width <= height || !presenter.src || presenter.dur <= 0) return null;
  const h = figureHeight(presenter, minTop);
  const w = (h * height * 0.6 * (1 - BLEED)) / width;
  const shown = h * (1 - SINK);
  return [presenter.side === 'left' ? 0 : 1 - w, 1 - shown, w, shown];
};

export const PresenterLayer: React.FC<Props> = ({ presenter, words, assetBaseUrl, minTop }) => {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const base = assetUrl(presenter.src, assetBaseUrl);
  const url = base && presenter.rev && !/^(data:|blob:)/.test(base) ? `${base}${base.includes('?') ? '&' : '?'}v=${presenter.rev}` : base;
  const f = frame - Math.round(presenter.from);
  const dur = Math.round(presenter.dur);
  // Vertical (Shorts): sem avatar. Fora da janela: nada (a <Img> só carrega quando aparece).
  if (!url || width <= height || dur <= 0 || f < 0 || f >= dur) return null;

  const scale = height / 1080;
  const explica = presenter.mode === 'explica';
  const left = presenter.side === 'left';
  // Altura da figura; a cabeça não passa da faixa do topo (a figura afunda 8% abaixo da borda).
  const h = Math.round(figureHeight(presenter, minTop) * height);

  // Entra por baixo com mola (~8 quadros), a inclinação assenta e ele flutua leve; sai descendo.
  const enter = spring({ frame: f, fps, config: { damping: 13, stiffness: 170 }, durationInFrames: 8 });
  const exit = interpolate(f, [dur - EXIT, dur], [0, 1], { ...clamp, easing: Easing.in(Easing.cubic) });
  const tilt = left ? 1 : -1; // inclina para o centro do quadro
  const rot = tilt * (interpolate(enter, [0, 1], [9, 1.5]) + Math.sin(f / 19) * 0.8);
  const travel = h + 60 * scale;
  const y = (1 - enter) * travel + exit * travel + Math.sin(f / (explica ? 14 : 11)) * 5 * scale;
  const punch = presenter.punch ? interpolate(f, [5, 9, 16], [1, 1.07, 1], clamp) : 1;
  const talk = explica ? speechPulse(words, frame, fps) : 0;
  const bleed = `${left ? -BLEED * 100 : BLEED * 100}%`;

  return (
    <div
      style={{
        position: 'absolute',
        [left ? 'left' : 'right']: 0,
        bottom: -Math.round(h * SINK),
        height: h,
        pointerEvents: 'none',
        transform: `translateX(${bleed}) translateY(${y}px) rotate(${rot}deg) scale(${punch * (1 + talk * 0.018)})`,
        transformOrigin: '50% 100%',
      }}
    >
      <Img src={url} style={{ height: h, width: 'auto', display: 'block', filter: stickerFilter(presenter.outline ?? 5, scale) }} />
    </div>
  );
};
