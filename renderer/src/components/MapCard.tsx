import React, { useMemo } from 'react';
import { AbsoluteFill, Easing, interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { fontStack } from '../fonts';
import {
  boxOf,
  centerLon,
  type GeoShape,
  hasRegion,
  isUsState,
  labelPoint,
  mainPolygons,
  meanLon,
  pathOf,
  projectedLayer,
  type Pt,
} from '../geo';
import type { TimelineScene } from '../types';
import { type CardViewProps, cardWeight, fitSize, mix, upper, withAlpha } from './DataCards';

// Cartão de mapa (família de dados): a tela inteira vira um mapa que aproxima das regiões citadas, pinta cada uma na
// cor de destaque e põe o rótulo (nome e valor) em cima. Sem tiles nem biblioteca: os contornos vêm embutidos.

export type MapItem = { region: string; label: string; value: string };

/** Regiões que o mapa sabe desenhar, na ordem do cartão, sem repetir. */
export const mapItems = (scene: TimelineScene, lang: string): MapItem[] => {
  const seen = new Set<string>();
  const out: MapItem[] = [];
  for (const it of scene.card?.items ?? []) {
    const region = it.region?.trim().toUpperCase();
    if (!hasRegion(region) || seen.has(region)) continue;
    seen.add(region);
    out.push({ region, label: upper((it.label ?? '').trim() || region, lang), value: upper((it.value ?? '').trim(), lang) });
    if (out.length === 6) break;
  }
  return out;
};

/** Posição da região em destaque entre as desenhadas: segue a região do item apontado (não a posição, que muda quando
 *  um item repetido ou que o mapa não sabe desenhar sai). */
export const mapMain = (scene: TimelineScene, items: MapItem[]): number | null => {
  const h = scene.card?.highlight;
  const region = typeof h === 'number' ? scene.card?.items?.[h]?.region?.trim().toUpperCase() : undefined;
  const at = region ? items.findIndex((it) => it.region === region) : -1;
  return at >= 0 ? at : null;
};

/** Menor largura (em unidades da projeção, ~0,35 rad) do enquadramento: país pequeno não vira um borrão gigante. */
const MIN_SPAN = 0.35;
/** Quanto o mapa começa mais afastado (e aproxima até o enquadramento final). */
const ZOOM_FROM = 2.6;

export const MapCard: React.FC<CardViewProps> = ({ scene, style, lang, band }) => {
  const frame = useCurrentFrame();
  const { fps, width, height, durationInFrames } = useVideoConfig();
  const items = mapItems(scene, lang);
  const u = Math.min(width, height);
  const portrait = height >= width;
  const font = style.font;
  const weight = cardWeight(font);
  const title = upper((scene.card?.label ?? '').trim(), lang);
  const main = mapMain(scene, items);
  const withStates = items.some((it) => isUsState(it.region));

  const geo = useMemo(() => {
    const lons = items.map((it) => centerLon(it.region)).filter((l): l is number => l !== null);
    const lon0 = meanLon(lons);
    const worldShapes = projectedLayer('world', lon0);
    const stateShapes = withStates ? projectedLayer('us', lon0) : [];
    const find = (id: string) => (isUsState(id) ? stateShapes : worldShapes).find((s) => s.id === id);
    const targets = items.map((it) => find(it.region)).filter((s): s is GeoShape => Boolean(s));
    return {
      worldPaths: worldShapes.map((s) => ({ id: s.id, d: pathOf(s) })),
      statePaths: stateShapes.map((s) => ({ id: s.id, d: pathOf(s) })),
      targets: targets.map((s) => ({ id: s.id, d: pathOf(s), at: labelPoint(s) })),
      box: boxOf(targets.flatMap((s) => mainPolygons(s).map((p) => p[0]))),
    };
  }, [items.map((it) => it.region).join(',')]);

  if (!items.length || !geo.targets.length) return null;

  // Área livre da tela (abaixo da faixa do topo e do título, acima da legenda), com margem dos lados.
  const titleSize = title ? fitSize(title, font, width * (portrait ? 0.86 : 0.6), u * (portrait ? 0.06 : 0.052), u * 0.032) : 0;
  const top = (band?.top ?? 0.06) * height + (title ? titleSize * 2.1 : 0);
  const bottom = (band?.bottom ?? 0.9) * height;
  const marginX = width * 0.08;
  const viewW = width - marginX * 2;
  const viewH = Math.max(u * 0.2, bottom - top);
  const { x0, y0, x1, y1 } = geo.box;
  const spanX = Math.max(MIN_SPAN, (x1 - x0) * 1.25);
  const spanY = Math.max(MIN_SPAN * 0.6, (y1 - y0) * 1.2);
  const fit = Math.min(viewW / spanX, viewH / spanY);
  const cx = (x0 + x1) / 2;
  const cy = (y0 + y1) / 2;
  const screenX = width / 2;
  const screenY = top + viewH / 2;
  // Aproxima em ~1,3 s (escala geométrica: o movimento parece constante) e segue num zoom lento até o fim da cena.
  const t = interpolate(frame, [0, fps * 1.3], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.inOut(Easing.cubic) });
  const drift = interpolate(frame, [fps * 1.3, Math.max(fps * 1.3 + 1, durationInFrames)], [1, 1.04], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  const scale = fit * Math.pow(ZOOM_FROM, t - 1) * drift;
  const toScreen = ([x, y]: Pt): Pt => [screenX + (x - cx) * scale, screenY + (y - cy) * scale];

  const ocean = mix(style.background, '#000000', 0.35);
  const land = mix(style.background, style.color, 0.16);
  const border = mix(style.background, style.color, 0.34);
  const lineW = Math.max(1, u * 0.0014);
  const fillAt = (i: number) => spring({ frame: frame - Math.round(fps * 0.7) - i * 6, fps, config: { damping: 200 }, durationInFrames: Math.round(fps * 0.5) });
  const order = main === null ? items.map((_, i) => i) : [main, ...items.map((_, i) => i).filter((i) => i !== main)];
  const colorOf = (i: number) => (main === null || i === main ? style.accent : mix(style.accent, land, 0.45));

  // Rótulos: um ponto no centro do maior polígono e a etiqueta ao lado dele, para fora do grupo de regiões (quem
  // está à esquerda do centro do enquadramento fica com a etiqueta à esquerda), sem sair da tela; etiqueta que
  // encostaria em outra já posta desce.
  const labelSize = Math.max(u * 0.026, Math.min(u * 0.042, u * (portrait ? 0.04 : 0.034)));
  const valueSize = labelSize * 1.35;
  const gap = u * 0.025;
  const edge = marginX * 0.5;
  type Label = { i: number; rank: number; ax: number; ay: number; left: number; y: number; boxW: number; boxH: number; right: boolean };
  const labels: Label[] = [];
  order.forEach((i, rank) => {
    const target = geo.targets.find((s) => s.id === items[i].region);
    if (!target) return;
    const [ax, ay] = toScreen(target.at);
    const item = items[i];
    const boxW = Math.max(item.label.length * 0.62 * labelSize, item.value.length * 0.62 * valueSize) + labelSize * 1.4;
    const boxH = labelSize * 1.5 + (item.value ? valueSize * 1.1 : 0);
    const fitsRight = ax + gap + boxW <= width - edge;
    const fitsLeft = ax - gap - boxW >= edge;
    const right = order.length === 1 || ax >= screenX - u * 0.02 ? fitsRight || !fitsLeft : !fitsLeft;
    const left = Math.max(edge, Math.min(width - edge - boxW, right ? ax + gap : ax - gap - boxW));
    labels.push({ i, rank, ax, ay, left, y: ay, boxW, boxH, right });
  });
  const placed: Label[] = [];
  for (const label of [...labels].sort((a, b) => a.ay - b.ay)) {
    for (const other of placed) {
      const across = label.left < other.left + other.boxW && other.left < label.left + label.boxW;
      const near = Math.abs(label.y - other.y) < (label.boxH + other.boxH) / 2 + u * 0.01;
      if (across && near) label.y = other.y + (label.boxH + other.boxH) / 2 + u * 0.01;
    }
    placed.push(label);
  }
  const pulse = (frame % Math.round(fps * 1.2)) / (fps * 1.2);
  const mainTarget = main === null ? null : geo.targets.find((s) => s.id === items[main].region);
  const mainAt = mainTarget ? toScreen(mainTarget.at) : null;

  const transform = `translate(${screenX} ${screenY}) scale(${scale}) translate(${-cx} ${-cy})`;
  return (
    <AbsoluteFill style={{ backgroundColor: ocean }}>
      <AbsoluteFill
        style={{
          background: `radial-gradient(ellipse at 50% 45%, ${withAlpha(mix(style.background, style.accent, 0.12), 0.9)} 0%, ${withAlpha(ocean, 0)} 70%)`,
        }}
      />
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} style={{ position: 'absolute', inset: 0 }}>
        <g transform={transform}>
          {geo.worldPaths.map((p, k) => (
            <path key={`w${k}`} d={p.d} fill={land} fillRule="evenodd" stroke={border} strokeWidth={lineW} vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
          ))}
          {geo.statePaths.map((p) => (
            <path key={p.id} d={p.d} fill={land} fillRule="evenodd" stroke={border} strokeWidth={lineW * 0.8} vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
          ))}
          {order.map((i, rank) => {
            const target = geo.targets.find((s) => s.id === items[i].region);
            if (!target) return null;
            const f = fillAt(rank);
            return (
              <path
                key={target.id}
                d={target.d}
                fill={mix(land, colorOf(i), f)}
                fillRule="evenodd"
                stroke={withAlpha(i === main || main === null ? style.accent : style.color, 0.4 + 0.6 * f)}
                strokeWidth={lineW * (1 + f)}
                vectorEffect="non-scaling-stroke"
                strokeLinejoin="round"
              />
            );
          })}
        </g>
      </svg>
      {mainAt && fillAt(0) > 0.6 ? (
        <div
          style={{
            position: 'absolute',
            left: mainAt[0],
            top: mainAt[1],
            width: u * 0.16,
            height: u * 0.16,
            marginLeft: -u * 0.08,
            marginTop: -u * 0.08,
            borderRadius: '50%',
            border: `${Math.max(2, u * 0.004)}px solid ${style.accent}`,
            transform: `scale(${0.2 + pulse})`,
            opacity: 0.8 * (1 - pulse),
          }}
        />
      ) : null}
      {labels.map(({ i, rank, y, ax, ay, boxH, left, right }) => {
        const item = items[i];
        const show = spring({ frame: frame - Math.round(fps * 0.9) - rank * 6, fps, config: { damping: 14, stiffness: 140, mass: 0.7 } });
        const topY = Math.max(top, Math.min(bottom - boxH, y - boxH / 2));
        const on = main === null || i === main;
        const dot = Math.max(8, u * 0.014);
        return (
          <React.Fragment key={item.region}>
          <div
            style={{
              position: 'absolute',
              left: ax - dot / 2,
              top: ay - dot / 2,
              width: dot,
              height: dot,
              borderRadius: '50%',
              backgroundColor: on ? style.color : withAlpha(style.color, 0.75),
              border: `${Math.max(2, u * 0.003)}px solid ${style.background}`,
              opacity: Math.min(1, show * 1.5),
            }}
          />
          <div
            style={{
              position: 'absolute',
              left,
              top: topY,
              padding: `${labelSize * 0.25}px ${labelSize * 0.55}px`,
              borderRadius: labelSize * 0.3,
              backgroundColor: withAlpha(style.background, 0.86),
              borderLeft: `${Math.max(3, u * 0.006)}px solid ${on ? style.accent : withAlpha(style.color, 0.5)}`,
              boxShadow: `0 ${u * 0.01}px ${u * 0.03}px rgba(0,0,0,0.4)`,
              fontFamily: fontStack(font),
              fontWeight: weight,
              lineHeight: 1.1,
              whiteSpace: 'nowrap',
              textAlign: 'left',
              opacity: Math.min(1, show * 1.3),
              transform: `scale(${interpolate(show, [0, 1], [0.7, 1])})`,
              transformOrigin: right ? 'left center' : 'right center',
            }}
          >
            <div style={{ fontSize: labelSize, letterSpacing: '0.03em', color: withAlpha(style.color, 0.95) }}>{item.label}</div>
            {item.value ? <div style={{ fontSize: valueSize, color: on ? style.accent : style.color, fontVariantNumeric: 'tabular-nums' }}>{item.value}</div> : null}
          </div>
          </React.Fragment>
        );
      })}
      {title ? (
        <div
          style={{
            position: 'absolute',
            left: portrait ? width * 0.07 : marginX * 0.5,
            right: portrait ? width * 0.07 : undefined,
            top: (band?.top ?? 0.06) * height + titleSize * 0.2,
            fontFamily: fontStack(font),
            fontWeight: weight,
            fontSize: titleSize,
            lineHeight: 1.1,
            letterSpacing: '0.03em',
            color: style.color,
            textAlign: portrait ? 'center' : 'left',
            textShadow: `0 ${u * 0.004}px ${u * 0.02}px rgba(0,0,0,0.6)`,
            opacity: interpolate(frame, [2, 12], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' }),
          }}
        >
          <span style={{ borderBottom: `${Math.max(3, u * 0.005)}px solid ${style.accent}`, paddingBottom: titleSize * 0.1 }}>{title}</span>
        </div>
      ) : null}
    </AbsoluteFill>
  );
};
