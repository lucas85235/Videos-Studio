import React from 'react';
import { AbsoluteFill, Freeze, useCurrentFrame, useVideoConfig } from 'remotion';
import type { TimelineFx } from '../types';
import { CAMERA_FX, cameraAt, isFreezeZoomMeme, isIdentity, remapFrame, timeSegments } from './fx/math';

type RigProps = {
  /** Eventos da cena (só os de câmera contam: punch, shake, zoom_rect, whip e o meme "congela + zoom"). */
  fx: TimelineFx[] | undefined;
  /** Duração da cena em quadros. */
  frames: number;
  /** Quadros de corte dentro da cena (início dos planos depois do 0): o punch e o zoom soltam neles. */
  cuts: number[];
  seed: number;
  /** Id único do filtro do whip na página. */
  id: string;
  children: React.ReactNode;
};

const hasCamera = (fx: TimelineFx[] | undefined) => (fx ?? []).some((e) => CAMERA_FX.has(e.type) || isFreezeZoomMeme(e));

/**
 * Câmera por eventos em volta do FUNDO da cena (imagem, planos, clipe, cartão de fundo): punch na palavra, tremor com
 * decaimento, zoom até um retângulo e whip (desfoque direcional no corte). Não mexe no interior das camadas: o
 * movimento próprio da imagem (Ken Burns, push, pan) continua lá dentro e a câmera soma por fora.
 *
 * Sem evento de câmera, devolve o fundo como está (mesma árvore de sempre).
 */
export const CameraRig: React.FC<RigProps> = ({ fx, frames, cuts, seed, id, children }) => {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  if (!hasCamera(fx)) return <>{children}</>;
  const cam = cameraAt(frame, fx, { fps, frames, cuts, width, height, seed });
  const still = isIdentity(cam);
  const filterId = `whip-${id.replace(/[^A-Za-z0-9_-]/g, '')}`;
  const blur = Math.round(cam.blur * 10) / 10;
  // A árvore fica a mesma em todos os quadros (só o estilo muda): trocar o invólucro remontaria a imagem.
  return (
    <AbsoluteFill style={{ overflow: 'hidden' }}>
      <svg width={0} height={0} style={{ position: 'absolute' }} aria-hidden="true">
        <defs>
          <filter id={filterId} x="-10%" y="0" width="120%" height="100%" colorInterpolationFilters="sRGB">
            <feGaussianBlur in="SourceGraphic" stdDeviation={`${blur} 0`} />
          </filter>
        </defs>
      </svg>
      <AbsoluteFill
        style={{
          transformOrigin: '0 0',
          transform: still ? undefined : `translate(${cam.x * 100}%, ${cam.y * 100}%) scale(${cam.scale})`,
          filter: blur > 0.05 ? `url(#${filterId})` : undefined,
        }}
      >
        <AbsoluteFill style={{ transform: Math.abs(cam.rot) > 1e-4 ? `rotate(${cam.rot}deg)` : undefined }}>{children}</AbsoluteFill>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

type WarpProps = {
  fx: TimelineFx[] | undefined;
  frames: number;
  cuts: number[];
  /** Início (quadro da cena) do trecho que está dentro (o plano): o tempo dele começa em 0 ali. */
  base?: number;
  children: React.ReactNode;
};

/**
 * Tempo do fundo: congela (`freeze`, meme "congela + zoom") e rampa de velocidade (`speed`, `params.rate`). O fundo
 * vê o quadro remapeado; imagem para a câmera, clipe para ou anda devagar. Depois do trecho, o material segue de onde
 * parou (fica atrás do relógio da cena). Sem esses eventos, devolve o fundo como está.
 */
export const TimeWarp: React.FC<WarpProps> = ({ fx, frames, cuts, base = 0, children }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const segs = timeSegments(fx, { fps, frames, cuts });
  if (!segs.length) return <>{children}</>;
  const local = remapFrame(base + frame, segs) - remapFrame(base, segs);
  return (
    <Freeze frame={Math.max(0, local)} active={Math.abs(local - frame) > 1e-6}>
      {children}
    </Freeze>
  );
};
