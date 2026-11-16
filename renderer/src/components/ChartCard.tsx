import React from 'react';
import { Easing, interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { fontStack } from '../fonts';
import type { ChartType, TextCardStyle, TimelineScene } from '../types';
import { barAmount, type CardViewProps, cardWeight, fitLines, fitSize, linesOf, mix, Panel, upper, useLayout, withAlpha } from './DataCards';

// Cartão de gráfico (família de dados): barras, linha, pizza e rosca, no mesmo painel dos cartões de dado.
// Rótulos e valores finais desde o primeiro quadro; só o desenho entra (barras crescem, linha se traça, fatias giram).

export type ChartRow = { label: string; value: string; amount: number };

/** Pontos do gráfico com número (o `amount` do backend; sem ele, o valor lido aqui). */
export const chartRows = (scene: TimelineScene, lang: string): ChartRow[] =>
  (scene.card?.items ?? [])
    .map((it) => {
      const value = (it.value ?? '').trim();
      const amount = typeof it.amount === 'number' && Number.isFinite(it.amount) ? it.amount : barAmount(value);
      return { label: upper((it.label ?? '').trim(), lang), value: upper(value, lang), amount: amount ?? NaN };
    })
    .filter((r) => Number.isFinite(r.amount))
    .slice(0, 8);

/** Tipo que dá para desenhar: pizza e rosca só com valores positivos; sem tipo, barras. */
export const chartTypeOf = (scene: TimelineScene, rows: ChartRow[]): ChartType => {
  const wanted = scene.card?.chart ?? 'bar';
  if ((wanted === 'pie' || wanted === 'donut') && !rows.every((r) => r.amount > 0)) return 'bar';
  return wanted;
};

const highlightOf = (scene: TimelineScene, n: number) => {
  const h = scene.card?.highlight;
  return typeof h === 'number' && h >= 0 && h < n ? h : null;
};

/** Cores das fatias: a de destaque (ou a primeira) na cor de destaque; as outras alternam tons do texto e do destaque. */
const sliceColors = (style: TextCardStyle, n: number, highlight: number | null) => {
  const first = highlight ?? 0;
  const tones = [0.2, 0.45, 0.6, 0.72, 0.82, 0.9];
  let j = 0;
  return Array.from({ length: n }, (_, i) => {
    if (i === first) return style.accent;
    const t = tones[Math.min(j, tones.length - 1)];
    const base = j % 2 === 0 ? style.color : style.accent;
    j += 1;
    return mix(base, style.background, t);
  });
};

type Metrics = { inner: number; k: number; u: number; portrait: boolean; font: string; weight: number };

// ---------------------------------------------------------------------------------------------
// Barras

const barMetrics = (rows: ChartRow[], m: Metrics) => {
  const { inner, u, k, font, portrait } = m;
  const gap = inner * (rows.length > 5 ? 0.025 : 0.05);
  const colW = (inner - gap * (rows.length - 1)) / rows.length;
  const valueSize = Math.min(...rows.map((r) => fitSize(r.value, font, colW * 1.05, u * 0.05 * k, u * 0.022)));
  const labelSize = Math.min(...rows.map((r) => fitLines(r.label, font, colW, u * 0.036 * k, u * 0.02, 2)));
  const labelLines = Math.max(1, ...rows.map((r) => linesOf(r.label, font, labelSize, colW, 2)));
  const plotH = inner * (portrait ? 0.62 : 0.32);
  const height = valueSize * 1.3 + plotH + labelSize * 1.1 * labelLines + u * 0.012;
  return { gap, colW, valueSize, labelSize, labelLines, plotH, height };
};

const BarChart: React.FC<{ rows: ChartRow[]; highlight: number | null; style: TextCardStyle; m: Metrics }> = ({ rows, highlight, style, m }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const { gap, valueSize, labelSize, labelLines, plotH } = barMetrics(rows, m);
  const max = Math.max(...rows.map((r) => r.amount), 1e-9);
  return (
    <div style={{ display: 'flex', alignItems: 'flex-end', gap, fontFamily: fontStack(m.font), fontWeight: m.weight, lineHeight: 1 }}>
      {rows.map((row, i) => {
        const grow = spring({ frame: frame - (3 + i * 4), fps, config: { damping: 200 }, durationInFrames: Math.round(fps * 0.7) });
        const share = Math.max(0.02, row.amount / max);
        const on = highlight == null || highlight === i;
        return (
          <div key={i} style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
            <div
              style={{
                fontSize: valueSize,
                height: valueSize * 1.3,
                color: highlight === i ? style.accent : style.color,
                fontVariantNumeric: 'tabular-nums',
                whiteSpace: 'nowrap',
                opacity: Math.min(1, grow * 2),
              }}
            >
              {row.value}
            </div>
            <div style={{ height: plotH, width: '100%', display: 'flex', alignItems: 'flex-end' }}>
              <div
                style={{
                  width: '100%',
                  height: `${100 * share * grow}%`,
                  borderRadius: `${m.u * 0.012}px ${m.u * 0.012}px 0 0`,
                  backgroundColor: on ? style.accent : withAlpha(style.color, 0.42),
                }}
              />
            </div>
            <div style={{ width: '100%', height: m.u * 0.004, backgroundColor: withAlpha(style.color, 0.35) }} />
            <div
              style={{
                marginTop: m.u * 0.008,
                minHeight: labelSize * 1.1 * labelLines,
                fontSize: labelSize,
                lineHeight: 1.1,
                letterSpacing: '0.02em',
                color: withAlpha(style.color, on ? 0.92 : 0.7),
                textAlign: 'center',
                overflowWrap: 'anywhere',
              }}
            >
              {row.label}
            </div>
          </div>
        );
      })}
    </div>
  );
};

// ---------------------------------------------------------------------------------------------
// Linha

const lineMetrics = (rows: ChartRow[], m: Metrics) => {
  const { inner, u, k, font, portrait } = m;
  const slot = inner / rows.length;
  const valueSize = Math.min(...rows.map((r) => fitSize(r.value, font, Math.max(slot * 1.4, inner * 0.18), u * 0.05 * k, u * 0.024)));
  const labelSize = Math.min(...rows.map((r) => fitSize(r.label, font, slot * 0.95, u * 0.036 * k, u * 0.02)));
  const height = inner * (portrait ? 0.78 : 0.42);
  return { valueSize, labelSize, height };
};

/** Domínio do eixo: começa no zero quando os valores são positivos e o menor não passa da metade do maior. */
const domainOf = (values: number[]) => {
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || Math.abs(max) || 1;
  const lo = min >= 0 && min <= max * 0.5 ? 0 : min - range * 0.15;
  const hi = max + range * 0.12;
  return { lo, hi };
};

const LineChart: React.FC<{ rows: ChartRow[]; highlight: number | null; style: TextCardStyle; m: Metrics }> = ({ rows, highlight, style, m }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const { valueSize, labelSize, height } = lineMetrics(rows, m);
  const w = m.inner;
  const left = valueSize * 1.2;
  const right = valueSize * 1.2;
  const top = valueSize * 1.6;
  const bottom = labelSize * 1.9;
  const plotW = w - left - right;
  const plotH = height - top - bottom;
  const { lo, hi } = domainOf(rows.map((r) => r.amount));
  const n = rows.length;
  const xOf = (i: number) => left + (n === 1 ? plotW / 2 : (plotW * i) / (n - 1));
  const yOf = (v: number) => top + plotH * (1 - (v - lo) / (hi - lo));
  const points = rows.map((r, i) => [xOf(i), yOf(r.amount)] as const);
  const lengths = points.slice(1).map(([x, y], i) => Math.hypot(x - points[i][0], y - points[i][1]));
  const total = lengths.reduce((a, b) => a + b, 0) || 1;
  const progress = interpolate(frame, [4, 4 + fps * 1.4], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.inOut(Easing.cubic) });
  // Até onde a linha chegou, em índice de ponto (fracionário), para os pontos e rótulos acenderem na hora certa.
  let reached = 0;
  let walked = 0;
  for (let i = 0; i < lengths.length; i += 1) {
    if (walked + lengths[i] <= progress * total) {
      walked += lengths[i];
      reached = i + 1;
    } else {
      reached = i + (progress * total - walked) / lengths[i];
      break;
    }
  }
  const path = points.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  const area = `${path} L${xOf(n - 1).toFixed(1)},${top + plotH} L${xOf(0).toFixed(1)},${top + plotH} Z`;
  const main = highlight ?? n - 1;
  const showValue = (i: number) => n <= 5 || i === 0 || i === main || i === n - 1;
  const showLabel = (i: number) => n <= 6 || i % 2 === 0 || i === n - 1;
  const stroke = Math.max(3, m.u * 0.008);
  const clipId = `chart-clip-${Math.round(m.inner)}-${n}`;
  const text = { fontFamily: fontStack(m.font), fontWeight: m.weight } as const;
  return (
    <svg width={w} height={height} viewBox={`0 0 ${w} ${height}`} style={{ display: 'block', overflow: 'visible' }}>
      <defs>
        <clipPath id={clipId}>
          <rect x={0} y={0} width={left + plotW * progress} height={height} />
        </clipPath>
        <linearGradient id={`${clipId}-fill`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={style.accent} stopOpacity={0.35} />
          <stop offset="100%" stopColor={style.accent} stopOpacity={0} />
        </linearGradient>
      </defs>
      {[0, 0.5, 1].map((t) => (
        <line key={t} x1={left} x2={left + plotW} y1={top + plotH * t} y2={top + plotH * t} stroke={withAlpha(style.color, t === 1 ? 0.35 : 0.12)} strokeWidth={Math.max(1, m.u * 0.002)} />
      ))}
      <path d={area} fill={`url(#${clipId}-fill)`} clipPath={`url(#${clipId})`} />
      <path
        d={path}
        fill="none"
        stroke={style.accent}
        strokeWidth={stroke}
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeDasharray={total}
        strokeDashoffset={total * (1 - progress)}
      />
      {points.map(([x, y], i) => {
        const lit = Math.max(0, Math.min(1, (reached - i) * 3 + 1));
        const big = i === main;
        return (
          <g key={i} opacity={lit}>
            <circle cx={x} cy={y} r={stroke * (big ? 2.2 : 1.5) * (0.6 + 0.4 * lit)} fill={big ? style.accent : style.background} stroke={style.accent} strokeWidth={stroke * 0.7} />
            {showValue(i) ? (
              <text x={x} y={y - stroke * 3.2} textAnchor="middle" fontSize={big ? valueSize : valueSize * 0.8} fill={big ? style.accent : style.color} style={text}>
                {rows[i].value}
              </text>
            ) : null}
          </g>
        );
      })}
      {rows.map((r, i) =>
        showLabel(i) ? (
          <text key={i} x={xOf(i)} y={top + plotH + labelSize * 1.4} textAnchor="middle" fontSize={labelSize} fill={withAlpha(style.color, 0.8)} style={text}>
            {r.label}
          </text>
        ) : null,
      )}
    </svg>
  );
};

// ---------------------------------------------------------------------------------------------
// Pizza e rosca

const pieMetrics = (rows: ChartRow[], m: Metrics) => {
  const { inner, u, k, font, portrait } = m;
  const diameter = portrait ? inner * 0.62 : inner * 0.42;
  const legendW = portrait ? inner : inner - diameter - inner * 0.06;
  const legendSize = Math.min(...rows.map((r) => fitSize(`${r.label} ${r.value}`, font, legendW * 0.8, u * 0.042 * k, u * 0.022)));
  const rowH = legendSize * 1.55;
  const legendH = rows.length * rowH;
  const height = portrait ? diameter + u * 0.04 + legendH : Math.max(diameter, legendH);
  return { diameter, legendW, legendSize, rowH, height };
};

/** Fatia entre os ângulos a0 e a1 (radianos, 0 = topo, sentido horário), com buraco de raio `r0` na rosca. */
const slicePath = (cx: number, cy: number, r: number, r0: number, a0: number, a1: number): string => {
  if (a1 - a0 >= Math.PI * 2 - 1e-4) {
    return `${slicePath(cx, cy, r, r0, a0, a0 + Math.PI)} ${slicePath(cx, cy, r, r0, a0 + Math.PI, a1 - 1e-4)}`;
  }
  const at = (radius: number, a: number) => `${(cx + radius * Math.sin(a)).toFixed(2)},${(cy - radius * Math.cos(a)).toFixed(2)}`;
  const large = a1 - a0 > Math.PI ? 1 : 0;
  const outer = `M${at(r, a0)} A${r},${r} 0 ${large} 1 ${at(r, a1)}`;
  if (r0 <= 0) return `${outer} L${cx},${cy} Z`;
  return `${outer} L${at(r0, a1)} A${r0},${r0} 0 ${large} 0 ${at(r0, a0)} Z`;
};

const PieChart: React.FC<{ rows: ChartRow[]; highlight: number | null; style: TextCardStyle; m: Metrics; donut: boolean }> = ({
  rows,
  highlight,
  style,
  m,
  donut,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const { diameter, legendW, legendSize, rowH, height } = pieMetrics(rows, m);
  const sweep = interpolate(frame, [3, 3 + fps * 1.1], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic) });
  const pop = spring({ frame: frame - Math.round(fps * 1.1), fps, config: { damping: 14 } });
  const total = rows.reduce((a, r) => a + r.amount, 0) || 1;
  const colors = sliceColors(style, rows.length, highlight);
  const r = diameter / 2;
  const r0 = donut ? r * 0.58 : 0;
  const main = highlight ?? rows.reduce((best, row, i) => (row.amount > rows[best].amount ? i : best), 0);
  let start = 0;
  const slices = rows.map((row, i) => {
    const a0 = start;
    const a1 = start + (row.amount / total) * Math.PI * 2;
    start = a1;
    return { i, a0, a1 };
  });
  const limit = sweep * Math.PI * 2;
  const gapLine = Math.max(2, m.u * 0.004);
  const text = { fontFamily: fontStack(m.font), fontWeight: m.weight } as const;
  const centerValue = rows[main].value;
  const centerSize = fitSize(centerValue, m.font, r0 * 1.5, m.u * 0.09, m.u * 0.03);
  const pie = (
    <svg width={diameter} height={diameter} viewBox={`${-diameter * 0.06} ${-diameter * 0.06} ${diameter * 1.12} ${diameter * 1.12}`} style={{ display: 'block', overflow: 'visible', flex: 'none' }}>
      {slices.map(({ i, a0, a1 }) => {
        if (limit <= a0) return null;
        const end = Math.min(a1, limit);
        const out = i === main ? r * 0.05 * pop : 0;
        const mid = (a0 + end) / 2;
        return (
          <path
            key={i}
            d={slicePath(r + out * Math.sin(mid), r - out * Math.cos(mid), r, r0, a0, end)}
            fill={colors[i]}
            stroke={withAlpha(style.background, 0.9)}
            strokeWidth={gapLine}
            strokeLinejoin="round"
          />
        );
      })}
      {donut ? (
        <text x={r} y={r + centerSize * 0.35} textAnchor="middle" fontSize={centerSize} fill={style.accent} opacity={pop} style={text}>
          {centerValue}
        </text>
      ) : null}
    </svg>
  );
  const legend = (
    <div style={{ width: legendW, display: 'flex', flexDirection: 'column', fontFamily: fontStack(m.font), fontWeight: m.weight, lineHeight: 1 }}>
      {rows.map((row, i) => {
        const show = interpolate(frame, [6 + i * 3, 14 + i * 3], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
        return (
          <div key={i} style={{ height: rowH, display: 'flex', alignItems: 'center', gap: legendSize * 0.5, opacity: show, fontSize: legendSize }}>
            <span style={{ width: legendSize * 0.8, height: legendSize * 0.8, borderRadius: legendSize * 0.18, backgroundColor: colors[i], flex: 'none' }} />
            <span style={{ flex: 1, minWidth: 0, textAlign: 'left', color: withAlpha(style.color, i === main ? 1 : 0.82), overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{row.label}</span>
            <span style={{ color: i === main ? style.accent : style.color, fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }}>{row.value}</span>
          </div>
        );
      })}
    </div>
  );
  return (
    <div style={{ height, display: 'flex', flexDirection: m.portrait ? 'column' : 'row', alignItems: 'center', justifyContent: 'center', gap: m.u * 0.04 }}>
      {pie}
      {legend}
    </div>
  );
};

// ---------------------------------------------------------------------------------------------
// Cartão

/** Gráfico de 2 a 8 pontos no painel dos cartões de dado; com menos de 2 números o SceneView mostra outro tipo. */
export const ChartCard: React.FC<CardViewProps> = ({ scene, style, lang, band }) => {
  const { portrait, u, panelW } = useLayout();
  const rows = chartRows(scene, lang);
  if (rows.length < 2) return null;
  const type = chartTypeOf(scene, rows);
  const highlight = highlightOf(scene, rows.length);
  const title = upper((scene.card?.label ?? '').trim(), lang);
  const k = portrait ? 1.15 : 0.8;
  const pad = u * 0.05;
  const inner = panelW - pad * 2;
  const font = style.font;
  const m: Metrics = { inner, k, u, portrait, font, weight: cardWeight(font) };
  const titleSize = fitLines(title, font, inner, u * 0.058 * k, u * 0.034, 2);
  const body =
    type === 'line' ? lineMetrics(rows, m).height : type === 'bar' ? barMetrics(rows, m).height : pieMetrics(rows, m).height;
  const estimate = body + (title ? linesOf(title, font, titleSize, inner, 2) * titleSize * 1.08 + pad * 0.6 : 0);
  return (
    <Panel style={style} scene={scene} lang={lang} pad={pad} band={band} estimate={estimate}>
      {title ? (
        <div
          style={{
            fontFamily: fontStack(font),
            fontWeight: m.weight,
            fontSize: titleSize,
            lineHeight: 1.08,
            letterSpacing: '0.03em',
            color: withAlpha(style.color, 0.9),
            marginBottom: pad * 0.6,
            textWrap: 'balance',
          }}
        >
          {title}
        </div>
      ) : null}
      {type === 'line' ? (
        <LineChart rows={rows} highlight={highlight} style={style} m={m} />
      ) : type === 'bar' ? (
        <BarChart rows={rows} highlight={highlight} style={style} m={m} />
      ) : (
        <PieChart rows={rows} highlight={highlight} style={style} m={m} donut={type === 'donut'} />
      )}
    </Panel>
  );
};
