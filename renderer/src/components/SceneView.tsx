import React from 'react';
import { AbsoluteFill } from 'remotion';
import { imageFilter } from '../look';
import type { RenderStyle, TimelineScene } from '../types';
import { Captions } from './Captions';
import { EffectsLayer } from './EffectsLayer';
import { CountCard, DocumentCard, KineticText, QuoteCard, TitleCard } from './TextCards';
import { TransitionIn } from './TransitionIn';
import { VisualLayer } from './VisualLayer';

type Props = { scene: TimelineScene; style: RenderStyle; assetBaseUrl: string | null | undefined };

export const assetUrl = (src: string | null, base: string | null | undefined) => {
  if (!src) return null;
  if (/^(https?:|data:|blob:)/.test(src)) return src;
  const clean = src.replace(/\\/g, '/').replace(/^\/+/, '');
  return `${(base ?? '/files/').replace(/\/?$/, '/')}${clean}`;
};

/** Uma cena: imagem com câmera + texturas + cartões de texto + legendas + transição de entrada. */
export const SceneView: React.FC<Props> = ({ scene, style, assetBaseUrl }) => {
  const url = assetUrl(scene.src, assetBaseUrl);
  const cards = style.textCards;
  const kind = scene.kind;
  const showCaptions = kind !== 'text' && kind !== 'quote';
  return (
    <AbsoluteFill style={{ backgroundColor: '#000', overflow: 'hidden' }}>
      {kind === 'document' ? (
        <DocumentCard url={url} scene={scene} style={cards} filter={imageFilter(style.effects, scene.look)} />
      ) : (
        <VisualLayer
          scene={scene}
          effects={style.effects}
          zoomStrength={style.zoomStrength}
          transitionFrames={style.transitionFrames}
          url={url}
          fallbackColor={cards.accent}
          dim={kind === 'title' || kind === 'count' || kind === 'quote' ? 0.55 : kind === 'text' ? 0.7 : 0}
        />
      )}
      <EffectsLayer effects={style.effects} look={scene.look} seed={scene.seed} />
      {kind === 'title' ? <TitleCard scene={scene} style={cards} /> : null}
      {kind === 'count' ? <CountCard scene={scene} style={cards} /> : null}
      {kind === 'quote' ? <QuoteCard scene={scene} style={cards} /> : null}
      {kind === 'text' ? <KineticText scene={scene} style={cards} uppercase={style.captions.uppercase} /> : null}
      {showCaptions ? <Captions lines={scene.captions} style={style.captions} /> : null}
      <TransitionIn type={scene.transition} frames={style.transitionFrames} seed={scene.seed} />
    </AbsoluteFill>
  );
};
