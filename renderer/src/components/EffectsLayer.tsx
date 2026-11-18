import React from 'react';
import { AbsoluteFill, Img, interpolate, random, staticFile, useCurrentFrame, useVideoConfig } from 'remotion';
import { activeGrade } from '../look';
import type { EffectSpec, SceneLook } from '../types';

/** `clean`: cena limpa (captura, gráfico, material próprio; `scene.clean`), sem grão nem vinheta. */
type Props = { effects: EffectSpec[]; look: SceneLook; seed: number; staticBase?: string | null; clean?: boolean };

/** Texturas que a cena limpa pula (efeitos por nível, ADR 0004): o resto do estilo continua. */
const DIRTY = new Set(['grain', 'vignette']);

const grainTexture = (index: number, base: string | null | undefined) =>
  base ? `${base.replace(/\/$/, '')}/textures/grain-${index}.png` : staticFile(`textures/grain-${index}.png`);

/** Camadas de textura sobre a imagem: grão, vinheta, scanlines, VHS, poeira, arranhões, vazamento de luz etc. */
export const EffectsLayer: React.FC<Props> = ({ effects, look, seed, staticBase, clean = false }) => {
  const frame = useCurrentFrame();
  const { width, height, fps } = useVideoConfig();
  const layers: React.ReactNode[] = [];
  const { grade, amount } = activeGrade(effects, look);
  if (grade.tint && amount > 0) {
    layers.push(
      <AbsoluteFill key="tint" style={{ backgroundColor: grade.tint.color(amount), mixBlendMode: grade.tint.blend as React.CSSProperties['mixBlendMode'] }} />,
    );
  }
  for (const [index, effect] of effects.entries()) {
    const a = effect.amount;
    const key = `${effect.type}-${index}`;
    if (clean && DIRTY.has(effect.type)) continue;
    switch (effect.type) {
      case 'grain': {
        // Texturas de ruído pré-geradas (assets/textures): bem mais leve que ruído SVG calculado a cada quadro.
        const url = grainTexture(frame % 4, staticBase);
        const tile = Math.max(128, Math.round(256 * (width / 1080)));
        const ox = Math.floor(random(`${seed}-gx-${frame}`) * tile);
        const oy = Math.floor(random(`${seed}-gy-${frame}`) * tile);
        layers.push(
          <AbsoluteFill
            key={key}
            style={{
              backgroundImage: `url(${url})`,
              backgroundSize: `${tile}px ${tile}px`,
              backgroundPosition: `${ox}px ${oy}px`,
              mixBlendMode: 'overlay',
              // Calibrado em cinza médio: desvio ≈ 2 + 12·amount (0,35 → ~6/255), média neutra.
              opacity: 0.05 + 0.28 * a,
            }}
          >
            <Img src={url} style={{ display: 'none' }} />
          </AbsoluteFill>,
        );
        break;
      }
      case 'vignette':
        layers.push(
          <AbsoluteFill
            key={key}
            style={{ background: `radial-gradient(ellipse at center, rgba(0,0,0,0) ${62 - 22 * a}%, rgba(0,0,0,${0.35 + 0.6 * a}) 100%)` }}
          />,
        );
        break;
      case 'scanlines': {
        const offset = (frame * 0.6) % 6;
        layers.push(
          <AbsoluteFill
            key={key}
            style={{
              backgroundImage: `repeating-linear-gradient(0deg, rgba(0,0,0,${0.12 + 0.35 * a}) 0px, rgba(0,0,0,${0.12 + 0.35 * a}) 1.5px, rgba(0,0,0,0) 3px, rgba(0,0,0,0) 6px)`,
              backgroundPosition: `0 ${offset}px`,
              mixBlendMode: 'multiply',
            }}
          />,
        );
        break;
      }
      case 'vhs': {
        const bandH = height * (0.05 + 0.06 * random(`${seed}-bh`));
        const speed = height / (fps * 3.5);
        const top = ((frame * speed + random(`${seed}-bt`) * height) % (height + bandH * 2)) - bandH;
        layers.push(
          <AbsoluteFill key={key} style={{ pointerEvents: 'none' }}>
            <div
              style={{
                position: 'absolute',
                left: 0,
                right: 0,
                top,
                height: bandH,
                opacity: 0.25 + 0.45 * a,
                background:
                  'repeating-linear-gradient(90deg, rgba(255,255,255,0.18) 0px, rgba(0,0,0,0.25) 3px, rgba(255,255,255,0.08) 7px, rgba(0,0,0,0.2) 11px)',
                mixBlendMode: 'screen',
                filter: 'blur(1px)',
              }}
            />
            <AbsoluteFill style={{ backgroundColor: `rgba(255,0,120,${0.03 * a})`, mixBlendMode: 'screen' }} />
          </AbsoluteFill>,
        );
        break;
      }
      case 'flicker': {
        const r = random(`${seed}-f-${frame}`);
        layers.push(<AbsoluteFill key={key} style={{ backgroundColor: `rgba(0,0,0,${(r > 0.85 ? 0.35 : r * 0.12) * a})` }} />);
        break;
      }
      case 'dust': {
        const specks = Math.round(3 + 6 * a);
        layers.push(
          <AbsoluteFill key={key}>
            {Array.from({ length: specks }).map((_, i) => {
              const r = random(`${seed}-d-${frame}-${i}`);
              if (r < 0.4) return null;
              const size = 2 + random(`${seed}-ds-${frame}-${i}`) * 5;
              return (
                <div
                  key={i}
                  style={{
                    position: 'absolute',
                    left: random(`${seed}-dx-${frame}-${i}`) * width,
                    top: random(`${seed}-dy-${frame}-${i}`) * height,
                    width: size,
                    height: size,
                    borderRadius: '50%',
                    backgroundColor: r > 0.7 ? 'rgba(255,255,255,0.55)' : 'rgba(0,0,0,0.55)',
                  }}
                />
              );
            })}
          </AbsoluteFill>,
        );
        break;
      }
      case 'film_scratches': {
        const lines = Math.round(1 + 3 * a);
        layers.push(
          <AbsoluteFill key={key}>
            {Array.from({ length: lines }).map((_, i) => {
              if (random(`${seed}-s-${frame}-${i}`) < 0.55) return null;
              return (
                <div
                  key={i}
                  style={{
                    position: 'absolute',
                    top: 0,
                    bottom: 0,
                    left: random(`${seed}-sx-${frame}-${i}`) * width,
                    width: 1 + random(`${seed}-sw-${frame}-${i}`) * 1.5,
                    backgroundColor: 'rgba(255,255,255,0.22)',
                  }}
                />
              );
            })}
          </AbsoluteFill>,
        );
        break;
      }
      case 'light_leak': {
        const x = interpolate(Math.sin(frame / (fps * 2.2) + seed), [-1, 1], [10, 90]);
        layers.push(
          <AbsoluteFill
            key={key}
            style={{
              background: `radial-gradient(ellipse at ${x}% 20%, rgba(255,150,60,${0.35 * a}) 0%, rgba(255,80,40,${0.12 * a}) 35%, rgba(0,0,0,0) 65%)`,
              mixBlendMode: 'screen',
            }}
          />,
        );
        break;
      }
      case 'halftone':
        layers.push(
          <AbsoluteFill
            key={key}
            style={{
              backgroundImage: 'radial-gradient(rgba(0,0,0,0.55) 30%, rgba(0,0,0,0) 32%)',
              backgroundSize: `${Math.round(5 + 6 * (1 - a))}px ${Math.round(5 + 6 * (1 - a))}px`,
              mixBlendMode: 'multiply',
              opacity: 0.3 + 0.4 * a,
            }}
          />,
        );
        break;
      case 'duotone':
        layers.push(
          <AbsoluteFill
            key={key}
            style={{ background: 'linear-gradient(135deg, #1b0f3a 0%, #c9a227 100%)', mixBlendMode: 'color', opacity: 0.4 + 0.5 * a }}
          />,
        );
        break;
      case 'blur_edges':
        layers.push(
          <AbsoluteFill
            key={key}
            style={{
              backdropFilter: `blur(${4 + 8 * a}px)`,
              WebkitMaskImage: 'radial-gradient(ellipse at center, rgba(0,0,0,0) 55%, rgba(0,0,0,1) 100%)',
              maskImage: 'radial-gradient(ellipse at center, rgba(0,0,0,0) 55%, rgba(0,0,0,1) 100%)',
            }}
          />,
        );
        break;
      default:
        break;
    }
  }
  return <AbsoluteFill style={{ pointerEvents: 'none' }}>{layers}</AbsoluteFill>;
};
