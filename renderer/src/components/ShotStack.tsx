import React from 'react';
import { AbsoluteFill, Sequence, useCurrentFrame } from 'remotion';
import type { EffectSpec, ShotEasing, TimelineFx, TimelineScene } from '../types';
import { TimeWarp } from './CameraRig';
import { ClipLayer } from './ClipLayer';
import { framingTransform, isEasing, type ShotSlot } from './fx/math';
import { assetUrl } from './RealMedia';

type Props = {
  scene: TimelineScene;
  /** Planos já normalizados (`shotSlots`): em ordem, sem buraco. */
  slots: ShotSlot[];
  fx: TimelineFx[] | undefined;
  cuts: number[];
  effects: EffectSpec[];
  assetBaseUrl: string | null | undefined;
  /** Imagem com câmera (o `VisualLayer` da cena, com o escurecimento e o desfoque do tipo dela). */
  renderImage: (sub: TimelineScene, url: string | null, overlay: React.ReactNode, easing: ShotEasing | undefined) => React.ReactNode;
  /**
   * Fundo da própria cena quando ele não é a imagem com câmera (lousa, colagem, tela, documento, vídeo da cena): os
   * planos `self` mostram esse fundo no tempo da cena (o desenho não recomeça a cada corte). Null: a cena é imagem.
   */
  selfBackground: React.ReactNode | null;
  /** Camada presa à imagem (marca-texto, seta, círculo) para um contêiner que começa no quadro `offset` da cena. */
  overlayAt?: (offset: number) => React.ReactNode;
};

/** Enquadramento do plano (escala e centro): um invólucro só quando não é o quadro inteiro. */
const Framed: React.FC<{ transform: string | null; children: React.ReactNode }> = ({ transform, children }) =>
  transform ? <AbsoluteFill style={{ transformOrigin: '0 0', transform }}>{children}</AbsoluteFill> : <>{children}</>;

/**
 * Vários planos dentro da cena (jump cut, B-roll do acervo, reenquadramento, clipe real): uma `Sequence` por plano,
 * com corte seco entre eles (o whip, quando há, é a câmera do CameraRig em volta). Cada plano usa a imagem com câmera
 * (`VisualLayer`, com o `motion`, o `easing` e o `framing` dele) ou o `ClipLayer` (vídeo do plano, mudo, com entrada,
 * velocidade e segurar no fim). Sem planos, a cena não passa por aqui (o caminho de sempre).
 */
export const ShotStack: React.FC<Props> = ({ scene, slots, fx, cuts, effects, assetBaseUrl, renderImage, selfBackground, overlayAt }) => {
  const frame = useCurrentFrame();
  const frames = scene.durationInFrames;
  const sceneUrl = assetUrl(scene.src, assetBaseUrl);
  return (
    <AbsoluteFill style={{ backgroundColor: '#000' }}>
      {slots.map((slot, i) => {
        const shot = slot.shot;
        const len = slot.to - slot.from;
        const video = shot?.video ? assetUrl(shot.video, assetBaseUrl) : null;
        const image = !video && shot?.src ? assetUrl(shot.src, assetBaseUrl) : null;
        const framing = framingTransform(shot?.framing);
        if (!video && !image && selfBackground) {
          // Plano da própria cena que não é imagem com câmera: o fundo dela no tempo da cena, só enquanto o plano vale.
          if (frame < slot.from || frame >= slot.to) return null;
          return (
            <AbsoluteFill key={`self-${i}`}>
              <Framed transform={framing}>
                {selfBackground}
                {overlayAt?.(0)}
              </Framed>
            </AbsoluteFill>
          );
        }
        const overlay = overlayAt?.(slot.from);
        const sub: TimelineScene = {
          ...scene,
          durationInFrames: len,
          motion: shot?.motion || scene.motion,
          // A transição de entrada (zoom_blur, glitch) é da cena: só o primeiro plano a mostra.
          transition: slot.from === 0 ? scene.transition : 'cut',
          // Lado do Ken Burns e do drift variam de plano para plano.
          seed: scene.seed + i * 7919,
          parallax: undefined,
        };
        let content: React.ReactNode;
        if (video) {
          content = (
            <ClipLayer
              scene={{ ...sub, video: shot?.video, video_seconds: undefined }}
              effects={effects}
              url={video}
              clipIn={shot?.clipIn}
              speed={shot?.speed}
              hold={shot?.hold}
              seconds={shot?.seconds}
              overlay={overlay}
            />
          );
        } else if (image) {
          // Imagem de outra origem (acervo, reaproveitada): o recorte e o foco da cena não valem para ela.
          content = renderImage({ ...sub, src: shot?.src ?? null, frame: undefined, focus: undefined }, image, overlay, easingOf(shot?.easing));
        } else {
          content = renderImage(sub, sceneUrl, overlay, easingOf(shot?.easing));
        }
        return (
          <Sequence key={`shot-${i}`} from={slot.from} durationInFrames={len} layout="none" name={`Plano ${i + 1}`}>
            <AbsoluteFill>
              <TimeWarp fx={fx} frames={frames} cuts={cuts} base={slot.from}>
                <Framed transform={framing}>{content}</Framed>
              </TimeWarp>
            </AbsoluteFill>
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};

const easingOf = (e: unknown): ShotEasing | undefined => (isEasing(e) ? e : undefined);
