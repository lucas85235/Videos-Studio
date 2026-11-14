import React from 'react';
import { AbsoluteFill, Sequence, useCurrentFrame, useVideoConfig } from 'remotion';
import { SceneView } from './components/SceneView';
import { ensureFonts } from './fonts';
import type { TimelineProps } from './types';

const ProgressBar: React.FC<{ color: string }> = ({ color }) => {
  const frame = useCurrentFrame();
  const { durationInFrames, width, height } = useVideoConfig();
  const p = frame / Math.max(1, durationInFrames - 1);
  return (
    <div style={{ position: 'absolute', left: 0, bottom: 0, height: Math.max(6, height * 0.006), width: width * p, backgroundColor: color }} />
  );
};

/**
 * Composição principal. No render ela recebe um BLOCO (isChunk=true, sem barra de progresso, que entra
 * na montagem final pelo FFmpeg). Na prévia do navegador recebe a timeline inteira.
 */
export const VideoTimeline: React.FC<TimelineProps> = (props) => {
  const { style } = props;
  ensureFonts([style.captions.font, style.textCards.font], props.fontBaseUrl);
  return (
    <AbsoluteFill style={{ backgroundColor: '#000' }}>
      {props.scenes.map((scene) => (
        <Sequence key={scene.id} from={scene.from} durationInFrames={scene.durationInFrames} layout="none">
          <SceneView scene={scene} style={style} assetBaseUrl={props.assetBaseUrl} staticBase={props.fontBaseUrl} />
        </Sequence>
      ))}
      {!props.isChunk && style.progressBar ? <ProgressBar color={style.progressColor} /> : null}
    </AbsoluteFill>
  );
};
