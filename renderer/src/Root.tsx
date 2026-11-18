import React from 'react';
import { Composition } from 'remotion';
import { sampleDataProps, sampleFxProps, sampleProps } from './sample';
import type { TimelineProps } from './types';
import { VideoTimeline } from './VideoTimeline';

const metadata = ({ props }: { props: TimelineProps }) => ({
  durationInFrames: Math.max(1, props.durationInFrames),
  fps: props.fps,
  width: props.width,
  height: props.height,
});

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="Timeline"
        component={VideoTimeline}
        durationInFrames={sampleProps.durationInFrames}
        fps={sampleProps.fps}
        width={sampleProps.width}
        height={sampleProps.height}
        defaultProps={sampleProps}
        calculateMetadata={metadata}
      />
      {/* Exemplo da família de dados e geografia (gráficos e mapas), só para o Remotion Studio. */}
      <Composition
        id="Dados"
        component={VideoTimeline}
        durationInFrames={sampleDataProps.durationInFrames}
        fps={sampleDataProps.fps}
        width={sampleDataProps.width}
        height={sampleDataProps.height}
        defaultProps={sampleDataProps}
        calculateMetadata={metadata}
      />
      {/* Edição (ADR 0004): planos, câmera por eventos, eventos de tela e clipe (servido pelo scripts/fx-stills.mjs). */}
      <Composition
        id="Edicao"
        component={VideoTimeline}
        durationInFrames={sampleFxProps.durationInFrames}
        fps={sampleFxProps.fps}
        width={sampleFxProps.width}
        height={sampleFxProps.height}
        defaultProps={sampleFxProps}
        calculateMetadata={metadata}
      />
    </>
  );
};
