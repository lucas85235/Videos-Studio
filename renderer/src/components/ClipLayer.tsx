import React from 'react';
import { AbsoluteFill, Freeze, OffthreadVideo, useVideoConfig } from 'remotion';
import { imageFilter } from '../look';
import type { EffectSpec, TimelineScene } from '../types';

type Props = {
  scene: TimelineScene;
  effects: EffectSpec[];
  /** Endereço do clipe (já resolvido pelo assetUrl). */
  url: string;
  dim?: number;
  /** Entrada no arquivo, em segundos (plano de clipe real: `TimelineShot.clipIn`). Ausente: do começo. */
  clipIn?: number;
  /** Velocidade pedida (0,25 a 2). Ausente: 1. */
  speed?: number;
  /**
   * Segura no último quadro: toca na velocidade pedida e congela quando o trecho acaba. Sem `hold`, o clipe curto fica
   * mais lento para cobrir (até MIN_CLIP_RATE) e só então congela, como o vídeo IA da cena-chave.
   */
  hold?: boolean;
  /** Segundos úteis do clipe a partir de `clipIn`. Ausente: `scene.video_seconds` (vídeo IA) ou desconhecido. */
  seconds?: number;
  /** Camada presa à imagem (marca-texto, seta, círculo), por cima do vídeo. */
  overlay?: React.ReactNode;
};

/** O clipe nunca fica mais lento que isto para cobrir a cena; o que faltar, o último quadro segura. */
export const MIN_CLIP_RATE = 0.5;

/**
 * Velocidade e quantos quadros da cena o clipe toca. Clipe mais longo que a cena: velocidade normal, corta no fim.
 * Mais curto: fica mais lento (até MIN_CLIP_RATE) e, se ainda faltar, congela no último quadro.
 */
export const clipTiming = (clipSeconds: number | undefined, sceneFrames: number, fps: number) => {
  if (!clipSeconds || clipSeconds <= 0) return { rate: 1, playFrames: sceneFrames };
  const clipFrames = Math.max(1, Math.floor(clipSeconds * fps));
  if (clipFrames >= sceneFrames) return { rate: 1, playFrames: sceneFrames };
  const rate = Math.max(MIN_CLIP_RATE, clipFrames / sceneFrames);
  // Um quadro de folga: o último quadro do arquivo às vezes não decodifica.
  const playFrames = Math.min(sceneFrames, Math.max(1, Math.floor(clipFrames / rate) - 1));
  return { rate, playFrames };
};

/**
 * Velocidade e quadros tocados de um clipe com velocidade pedida. `hold`: na velocidade pedida e congela no fim do
 * trecho; sem `hold`: a conta do `clipTiming` no tempo do clipe já acelerado (só desacelera para cobrir).
 */
export const shotClipTiming = (clipSeconds: number | undefined, frames: number, fps: number, speed = 1, hold = false) => {
  const base = speed > 0 ? speed : 1;
  if (hold) {
    if (!clipSeconds || clipSeconds <= 0) return { rate: base, playFrames: frames };
    const clipFrames = Math.max(1, Math.floor((clipSeconds * fps) / base));
    return { rate: base, playFrames: Math.min(frames, Math.max(1, clipFrames - 1)) };
  }
  const { rate, playFrames } = clipTiming(clipSeconds ? clipSeconds / base : clipSeconds, frames, fps);
  return { rate: rate * base, playFrames };
};

/**
 * Vídeo no lugar da imagem: o vídeo IA da cena-chave ou o clipe real do plano (qualquer tipo de cena), sempre mudo (a
 * trilha é a mixagem), com a gradação do estilo.
 */
export const ClipLayer: React.FC<Props> = ({ scene, effects, url, dim = 0, clipIn, speed, hold, seconds, overlay }) => {
  const { fps } = useVideoConfig();
  const { rate, playFrames } = shotClipTiming(seconds ?? scene.video_seconds, scene.durationInFrames, fps, speed ?? 1, hold ?? false);
  const filter = `${imageFilter(effects, scene.look)}${dim ? ` brightness(${1 - dim})` : ''}`.trim();
  const trim = clipIn && clipIn > 0 ? Math.round(clipIn * fps) : undefined;
  return (
    <AbsoluteFill style={{ overflow: 'hidden', backgroundColor: '#000' }}>
      <Freeze frame={Math.max(0, playFrames - 1)} active={(f) => f >= playFrames}>
        <OffthreadVideo
          src={url}
          muted
          trimBefore={trim}
          playbackRate={rate}
          style={{ width: '100%', height: '100%', objectFit: 'cover', filter: filter || undefined }}
        />
      </Freeze>
      {overlay}
    </AbsoluteFill>
  );
};
