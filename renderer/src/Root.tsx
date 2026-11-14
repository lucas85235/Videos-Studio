import React from 'react';
import { Composition } from 'remotion';
import { sampleProps } from './sample';
import type { TimelineProps } from './types';
import { VideoTimeline } from './VideoTimeline';

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="Timeline"
      component={VideoTimeline}
      durationInFrames={sampleProps.durationInFrames}
      fps={sampleProps.fps}
      width={sampleProps.width}
      height={sampleProps.height}
      defaultProps={sampleProps}
      calculateMetadata={({ props }: { props: TimelineProps }) => ({
        durationInFrames: Math.max(1, props.durationInFrames),
        fps: props.fps,
        width: props.width,
        height: props.height,
      })}
    />
  );
};
