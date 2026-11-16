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

/** Vídeo IA da cena-chave no lugar da imagem: sem som (a trilha é a mixagem), com a gradação do estilo. */
export const ClipLayer: React.FC<Props> = ({ scene, effects, url, dim = 0 }) => {
  const { fps } = useVideoConfig();
  const { rate, playFrames } = clipTiming(scene.video_seconds, scene.durationInFrames, fps);
  const filter = `${imageFilter(effects, scene.look)}${dim ? ` brightness(${1 - dim})` : ''}`.trim();
  return (
    <AbsoluteFill style={{ overflow: 'hidden', backgroundColor: '#000' }}>
      <Freeze frame={Math.max(0, playFrames - 1)} active={(f) => f >= playFrames}>
        <OffthreadVideo
          src={url}
          muted
          playbackRate={rate}
          style={{ width: '100%', height: '100%', objectFit: 'cover', filter: filter || undefined }}
        />
      </Freeze>
    </AbsoluteFill>
  );
};
