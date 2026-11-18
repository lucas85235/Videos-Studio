import React from 'react';
import { AbsoluteFill, Freeze, OffthreadVideo, useVideoConfig } from 'remotion';
import { imageFilter } from '../look';
import type { EffectSpec, TimelineScene } from '../types';
import { shotClipTiming } from './fx/math';

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

// Contas do tempo do clipe moram em fx/math.ts (rodam no teste do Node); reexportadas daqui.
export { clipTiming, MIN_CLIP_RATE, shotClipTiming } from './fx/math';

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
