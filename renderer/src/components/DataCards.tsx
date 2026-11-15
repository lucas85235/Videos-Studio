import React from 'react';
import { AbsoluteFill, interpolate, random, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { fontStack, fontWeight } from '../fonts';
import type { TextCardStyle, TimelineCardItem, TimelineScene } from '../types';
import { useEntrance } from './TextCards';

// Cartões de dado (stat, compare) e sobreposições (faixa, carimbo, fonte), mais o fundo animado.
// Medidas em `u` = menor lado do quadro, para o mesmo desenho servir em 9:16 e 16:9.

/** Faixa livre da tela para o painel, em fração da altura: de baixo da faixa do topo até acima da legenda. */
export type CardBand = { top: number; bottom: number };

export type CardViewProps = { scene: TimelineScene; style: TextCardStyle; uppercase: boolean; lang: string; band?: CardBand };

// ---------------------------------------------------------------------------------------------
// Cores

const HEX6 = /^#([0-9a-f]{6})([0-9a-f]{2})?$/i;
const HEX3 = /^#([0-9a-f])([0-9a-f])([0-9a-f])$/i;

const hex6 = (color: string) => {
  const c = color.trim();
  const m3 = HEX3.exec(c);
  if (m3) return `#${m3[1]}${m3[1]}${m3[2]}${m3[2]}${m3[3]}${m3[3]}`;
  const m6 = HEX6.exec(c);
  return m6 ? `#${m6[1]}` : null;
};

/** Cor com transparência (aceita #RGB, #RRGGBB, #RRGGBBAA e, por reserva, qualquer cor CSS). */
export const withAlpha = (color: string, a: number) => {
  const h = hex6(color);
  const alpha = Math.max(0, Math.min(1, a));
  if (h) return `${h}${Math.round(alpha * 255).toString(16).padStart(2, '0')}`;
  return `color-mix(in srgb, ${color} ${Math.round(alpha * 100)}%, transparent)`;
};

/** Mistura de duas cores (t = 0 é a primeira). */
const mix = (a: string, b: string, t: number) => {
  const ha = hex6(a);
  const hb = hex6(b);
  if (!ha || !hb) return `color-mix(in srgb, ${a} ${Math.round((1 - t) * 100)}%, ${b})`;
  const ch = (h: string, i: number) => parseInt(h.slice(1 + i * 2, 3 + i * 2), 16);
  const out = [0, 1, 2].map((i) => Math.round(ch(ha, i) * (1 - t) + ch(hb, i) * t).toString(16).padStart(2, '0'));
  return `#${out.join('')}`;
};

// ---------------------------------------------------------------------------------------------
// Texto

/** Largura média de maiúsculas e algarismos, em em, nas fontes do app (no peso usado nos cartões). */
const FONT_EM: Record<string, number> = {
  Anton: 0.48,
  'Bebas Neue': 0.4,
  Oswald: 0.52,
  Cinzel: 0.74,
  'IBM Plex Sans': 0.64,
  Montserrat: 0.74,
  'Noto Sans': 0.64,
  'Special Elite': 0.6,
  VT323: 0.42,
};

const charEm = (font: string) => FONT_EM[font] ?? 0.64;

/** Fontes de maiúscula baixa parecem menores no mesmo tamanho: ganham um pouco. */
const FONT_BOOST: Record<string, number> = { VT323: 1.3 };
const boost = (font: string) => FONT_BOOST[font] ?? 1;

/** Tamanho de fonte para `text` caber em `maxWidth` numa linha, entre `minSize` e `maxSize`. */
const fitSize = (text: string, font: string, maxWidth: number, maxSize: number, minSize = 0) =>
  Math.max(minSize * boost(font), Math.min(maxSize * boost(font), maxWidth / Math.max(1, text.length * charEm(font))));

/** Tamanho para caber em até `lines` linhas, quebrando nas palavras (estimativa conservadora). */
const fitLines = (text: string, font: string, maxWidth: number, maxSize: number, minSize: number, lines: number) => {
  const longest = text.split(/\s+/).reduce((m, w) => Math.max(m, w.length), 1);
  const perLine = Math.max(longest, Math.ceil((text.length * 1.12) / lines));
  return Math.max(minSize * boost(font), Math.min(maxSize * boost(font), maxWidth / (perLine * charEm(font))));
};

/** Prefere uma linha; só quebra (até `lines`) quando numa linha ficaria menor que `minSingle`. */
const fitPreferOne = (text: string, font: string, maxWidth: number, maxSize: number, minSize: number, lines: number, minSingle: number) => {
  const one = fitSize(text, font, maxWidth, maxSize, 0);
  return one >= minSingle * boost(font) ? one : fitLines(text, font, maxWidth, maxSize, minSize, lines);
};

const upper = (text: string, lang: string) => {
  try {
    return text.toLocaleUpperCase(lang);
  } catch {
    return text.toUpperCase();
  }
};

const cardWeight = (font: string) => fontWeight(font, 800);

const SOURCE_WORD: Record<string, string> = { pt: 'Fonte', es: 'Fuente', it: 'Fonte', en: 'Source', fr: 'Source', de: 'Quelle' };
const sourceText = (source: string, lang: string) => `${SOURCE_WORD[lang.slice(0, 2).toLowerCase()] ?? 'Source'}: ${source}`;

// ---------------------------------------------------------------------------------------------
// Números do cartão

export type ParsedValue = {
  prefix: string;
  sign: string;
  num: number;
  decimals: number;
  dec: string;
  group: string;
  suffix: string;
};

const NUM_RE = /^(\D*?)([+\-−]?\d(?:[\d.,]*\d)?)(.*)$/su;

/** Separa prefixo, número e sufixo ("R$ 1,5 BI" → "R$ ", 1.5, " BI"), guardando o formato do número. */
export const parseValue = (raw: string): ParsedValue | null => {
  const m = NUM_RE.exec(raw.trim());
  if (!m) return null;
  const [, prefix, numText, suffix] = m;
  const body = numText.replace(/^[+\-−]/, '');
  const dots = (body.match(/\./g) ?? []).length;
  const commas = (body.match(/,/g) ?? []).length;
  let dec = '';
  let group = '';
  if (dots && commas) {
    dec = body[Math.max(body.lastIndexOf('.'), body.lastIndexOf(','))];
    group = dec === '.' ? ',' : '.';
  } else if (dots + commas === 1) {
    const sep = dots ? '.' : ',';
    const [a, b] = body.split(sep);
    // "1.000" e "475,000" são milhar; "1,5" e "2.5" são decimais.
    if (b.length === 3 && a.length <= 3 && a !== '0') group = sep;
    else dec = sep;
  } else if (dots + commas > 1) {
    group = dots ? '.' : ',';
  }
  let clean = group ? body.split(group).join('') : body;
  if (dec) clean = clean.replace(dec, '.');
  const abs = Number(clean);
  if (!Number.isFinite(abs)) return null;
  const negative = /^[\-−]/.test(numText);
  return {
    prefix,
    sign: numText.startsWith('+') ? '+' : '',
    num: negative ? -abs : abs,
    decimals: dec ? (clean.split('.')[1] ?? '').length : 0,
    dec: dec || ',',
    group,
    suffix,
  };
};

/** Número no mesmo formato do original (separadores e casas decimais). */
export const formatValue = (v: number, p: ParsedValue) => {
  const [int, frac] = Math.abs(v).toFixed(p.decimals).split('.');
  const grouped = p.group ? int.replace(/\B(?=(\d{3})+(?!\d))/g, p.group) : int;
  return `${v < 0 ? '-' : p.sign}${grouped}${frac ? p.dec + frac : ''}`;
};

/** Ano (inteiro de 4 dígitos de 1000 a 2100, sem unidade nem moeda): não conta, entra com "pop". */
export const isYear = (p: ParsedValue) =>
  p.decimals === 0 && !p.group && p.num >= 1000 && p.num <= 2100 && !/[\p{L}%×]/u.test(p.suffix) && !/[$€£¥₹]/u.test(p.prefix);

/** Valor que não é uma quantidade só (horário, placar, razão, intervalo: "22H15", "10X1", "24/7", "10-12"). */
export const isCompound = (p: ParsedValue) => /\d/.test(p.suffix);

const SCALES: Array<[RegExp, number]> = [
  [/^(mil|k|thousand|thousands)$/i, 1e3],
  [/^(mi|mn|milhao|milhoes|million|millions|millon|millones)$/i, 1e6],
  [/^M$/, 1e6],
  [/^(bi|bn|b|bilhao|bilhoes|billion|billions)$/i, 1e9],
  [/^(tri|tn|trilhao|trilhoes|trillion|trillions)$/i, 1e12],
];

/** Unidades que dá para converter entre si nas barras: família e fator para a unidade base. */
const UNIT_FAMILIES: Record<string, [string, number]> = {
  s: ['tempo', 1], seg: ['tempo', 1], sec: ['tempo', 1], min: ['tempo', 60], h: ['tempo', 3600], hr: ['tempo', 3600],
  kb: ['dados', 1e3], mb: ['dados', 1e6], gb: ['dados', 1e9], tb: ['dados', 1e12],
  cm: ['distancia', 0.01], m: ['distancia', 1], km: ['distancia', 1e3],
  g: ['massa', 1], kg: ['massa', 1e3],
};

const CURRENCY = /(?:R|US|U|A|C)?\$|€|£|¥|₹/iu;

export type BarValue = {
  /** Grandeza na unidade base da família (ou na própria unidade). */
  amount: number;
  /** Unidade que sobra depois da escala ("tempo" para s/min/h, "%", ""): barras só entre unidades iguais. */
  unit: string;
  currency: string;
};

/** Grandeza para as barras do compare, com a unidade e a moeda ("475 MI" = 4,75e8 sem unidade; "2 min" = 120 s).
 *  Lê o valor original, antes das maiúsculas: "100 m" é 100 metros e "100 M" é 100 milhões. Mais estrito que o
 *  card_number do backend: horário, placar, intervalo, data e nome com número ("22h15", "3x1", "10-12",
 *  "DEZ 2023", "GTA 6") não viram barra e devolvem null. */
export const barValue = (raw: string): BarValue | null => {
  const p = parseValue(raw);
  if (!p || isCompound(p)) return null;
  if (/\p{L}/u.test(p.prefix.replace(new RegExp(CURRENCY.source, 'giu'), ''))) return null;
  const currency = (CURRENCY.exec(p.prefix)?.[0] ?? '').toUpperCase();
  const norm = (t: string) => t.normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^\p{L}%×]/gu, '');
  const tokens = p.suffix.trim().split(/[\s+]+/).map(norm).filter(Boolean);
  const scale = tokens.length ? SCALES.find(([re]) => re.test(tokens[0]))?.[1] : undefined;
  const unit = (scale ? tokens.slice(1) : tokens).join(' ').toLowerCase();
  const family = UNIT_FAMILIES[unit];
  return { amount: p.num * (scale ?? 1) * (family?.[1] ?? 1), unit: family?.[0] ?? unit, currency };
};

/** Só a grandeza (ver `barValue`). */
export const barAmount = (raw: string): number | null => barValue(raw)?.amount ?? null;

/** Valor que conta de 0 até o número em ~0,8 s (ano e número pequeno inteiro entram parados). */
const useCountedValue = (p: ParsedValue | null, delay: number) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  if (!p) return { text: '', counting: false };
  const counts = !isYear(p) && (Math.abs(p.num) >= 10 || p.decimals > 0);
  if (!counts) return { text: formatValue(p.num, p), counting: false };
  const k = spring({ frame: frame - delay, fps, config: { damping: 200 }, durationInFrames: Math.round(fps * 0.8) });
  return { text: formatValue(p.num * k, p), counting: true };
};

// ---------------------------------------------------------------------------------------------
// Peças comuns

const layoutOf = (width: number, height: number) => {
  const portrait = height >= width;
  const u = Math.min(width, height);
  const panelW = portrait ? width * 0.86 : Math.min(width * 0.6, height * 1.2);
  return { width, height, portrait, u, panelW };
};

const useLayout = () => {
  const { width, height } = useVideoConfig();
  return layoutOf(width, height);
};

/** Linhas que o texto ocupa em `maxWidth` com a fonte `size` (estimativa pela largura média). */
const linesOf = (text: string, font: string, size: number, maxWidth: number, max = 3) =>
  text ? Math.max(1, Math.min(max, Math.ceil((text.length * charEm(font) * size) / Math.max(1, maxWidth)))) : 0;

/** Tamanho e altura da faixa do topo (mesma conta do `Headline`), para o painel ficar embaixo dela. */
export const headlineBox = (text: string, style: TextCardStyle, lang: string, width: number, height: number) => {
  const { portrait, u } = layoutOf(width, height);
  const label = upper(text.trim(), lang);
  const maxW = width * (portrait ? 0.86 : 0.7);
  const padX = u * 0.035;
  const size = fitLines(label, style.font, maxW - padX * 2, u * (portrait ? 0.068 : 0.06), u * 0.04, 2);
  const lines = linesOf(label, style.font, size, maxW - padX * 2, 2);
  const boxH = lines * size * 1.05 + size * 0.34;
  return { label, maxW, padX, size, top: height * 0.1, bottom: height * 0.1 + boxH / 2 };
};

/** Tamanho do carimbo: uma linha; quebra em duas (palavra por linha) antes de ficar pequeno demais. */
const stampFit = (label: string, font: string, maxWidth: number, u: number) => {
  const textW = maxWidth * 0.8; // borda, respiro e espaçamento entre letras
  const one = fitSize(label, font, textW, u * 0.075, 0);
  const words = label.split(/\s+/).filter(Boolean);
  if (one >= u * 0.045 * boost(font) || words.length < 2) return { lines: [label], size: Math.max(u * 0.028, one) };
  let best = { lines: [label], size: one };
  for (let i = 1; i < words.length; i += 1) {
    const parts = [words.slice(0, i).join(' '), words.slice(i).join(' ')];
    const size = Math.min(...parts.map((p) => fitSize(p, font, textW, u * 0.075, 0)));
    if (size > best.size) best = { lines: parts, size };
  }
  return { lines: best.lines, size: Math.max(u * 0.028, best.size) };
};

/** Quanto o carimbo pousa sobre a borda de cima do painel (px): o resto dele fica acima do painel. */
const stampOverlap = (u: number) => u * 0.035;

/**
 * Quanto o carimbo ancorado sobe acima da borda de cima do painel (px): altura das linhas e da borda, menos a parte
 * que pousa no painel, mais o canto que a rotação de −10° levanta.
 */
const stampRise = (label: string, font: string, maxWidth: number, u: number) => {
  const { lines, size } = stampFit(label, font, maxWidth, u);
  const longest = Math.max(...lines.map((l) => l.length));
  const w = longest * (charEm(font) + 0.06) * size + size * 0.68;
  const h = lines.length * size + size * 0.26 + Math.max(3, u * 0.007) * 2;
  return Math.max(0, h - stampOverlap(u)) + w * 0.09;
};

/**
 * Painel do cartão: fundo do estilo translúcido, faixa de destaque no topo. Fica a 42% da altura quando cabe; senão,
 * no meio da faixa livre (`band`, entre a faixa do topo e a legenda) e, se ainda não couber, encolhe.
 * `estimate` é a altura prevista do conteúdo (px, sem o respiro do painel).
 */
const Panel: React.FC<{
  style: TextCardStyle;
  scene: TimelineScene;
  lang: string;
  pad: number;
  band?: CardBand;
  estimate: number;
  children: React.ReactNode;
}> = ({ style, scene, lang, pad, band, estimate, children }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const { width, height, u, panelW } = useLayout();
  const entrance: { transform?: string; opacity?: number } = useEntrance(style.animation, scene.seed);
  const bar = spring({ frame: frame - 3, fps, config: { damping: 200 }, durationInFrames: Math.round(fps * 0.5) });
  const radius = u * 0.028;
  const source = scene.card?.source?.trim();
  const stamp = scene.card?.stamp?.trim();
  const sourceSize = Math.max(14, u * 0.026);
  const panelH = estimate + pad * 2.15 + (source ? pad * 0.55 + sourceSize * 1.25 : 0);
  // O carimbo fica no canto de cima, saindo um pouco do painel: a faixa livre começa abaixo dele.
  const stampMax = panelW * 0.6;
  const top = (band?.top ?? 0.06) * height + (stamp ? stampRise(upper(stamp, lang), style.font, stampMax, u) : 0);
  const bottom = (band?.bottom ?? 0.9) * height;
  const room = Math.max(1, bottom - top);
  const fit = Math.min(1, room / panelH);
  const half = (panelH * fit) / 2;
  const center = Math.max(top + half, Math.min(bottom - half, height * 0.42));
  return (
    <AbsoluteFill>
      <div
        style={{
          position: 'absolute',
          left: (width - panelW) / 2,
          width: panelW,
          top: center,
          transform: `translateY(-50%) ${fit < 1 ? `scale(${fit}) ` : ''}${entrance.transform ?? ''}`,
          opacity: entrance.opacity,
        }}
      >
        <div
          style={{
            position: 'relative',
            overflow: 'hidden',
            borderRadius: radius,
            backgroundColor: withAlpha(style.background, 0.8),
            boxShadow: `0 ${u * 0.03}px ${u * 0.09}px rgba(0,0,0,0.45), inset 0 0 0 ${Math.max(1, u * 0.0016)}px ${withAlpha(style.color, 0.1)}`,
            padding: `${pad * 1.15}px ${pad}px ${pad}px`,
            textAlign: 'center',
          }}
        >
          <div
            style={{
              position: 'absolute',
              left: 0,
              top: 0,
              height: Math.max(4, u * 0.012),
              width: `${100 * bar}%`,
              backgroundColor: style.accent,
            }}
          />
          {children}
          {source ? (
            <div
              style={{
                marginTop: pad * 0.55,
                fontFamily: fontStack('Noto Sans'),
                fontWeight: 600,
                fontSize: Math.max(14, u * 0.026),
                letterSpacing: '0.02em',
                color: withAlpha(style.color, 0.62),
              }}
            >
              {sourceText(source, lang)}
            </div>
          ) : null}
        </div>
        {stamp ? (
          // No canto de cima (a faixa livre já desce o painel abaixo da faixa do topo), sem passar da largura dele.
          // Cresce para cima a partir da borda do painel: carimbo de 2 linhas não cobre o conteúdo.
          <Stamp
            text={stamp}
            style={style}
            lang={lang}
            delay={10}
            maxWidth={stampMax}
            anchor={{ position: 'absolute', right: 0, bottom: `calc(100% - ${stampOverlap(u)}px)` }}
          />
        ) : null}
      </div>
    </AbsoluteFill>
  );
};

// ---------------------------------------------------------------------------------------------
// Cartões

/** Número grande: conta até o valor (ano entra com "pop"), com rótulo embaixo. */
export const StatCard: React.FC<CardViewProps> = ({ scene, style, lang, band }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const { portrait, u, panelW } = useLayout();
  const card = scene.card ?? {};
  const raw = upper((card.value ?? (scene.number != null ? String(scene.number) : '')).trim(), lang);
  const label = upper((card.label ?? (card.value ? '' : scene.text)).trim(), lang);
  // Horário, placar, razão ("22H15", "10X1", "24/7"): o valor entra inteiro, com "pop", sem contar.
  const parsed = parseValue(raw);
  const p = parsed && !isCompound(parsed) ? parsed : null;
  const counted = useCountedValue(p, 4);
  const pad = u * (portrait ? 0.06 : 0.05);
  const inner = panelW - pad * 2;
  const font = style.font;
  const weight = cardWeight(font);
  const prefix = p?.prefix.trim() ?? '';
  const suffix = p?.suffix.trim() ?? '';
  const main = p ? formatValue(p.num, p) : raw;
  const affixRatio = 0.42;
  const affixChars = (prefix ? prefix.length + 0.6 : 0) + (suffix ? suffix.length + 0.6 : 0);
  const size = Math.min(u * (portrait ? 0.3 : 0.25) * boost(font), inner / Math.max(1, (main.length + affixChars * affixRatio) * charEm(font)));
  const pop = spring({ frame: frame - 2, fps, config: { damping: 11, stiffness: 140, mass: 0.7 } });
  const scale = counted.counting ? 1 : interpolate(pop, [0, 1], [1.5, 1]);
  const labelSize = raw
    ? fitLines(label, font, inner, u * (portrait ? 0.066 : 0.058), u * 0.04, 2)
    : fitLines(label, font, inner, u * 0.11, u * 0.05, 3);
  const affix: React.CSSProperties = { fontSize: size * affixRatio, color: style.accent, lineHeight: 1 };
  const estimate = (raw ? size : 0) + (label ? linesOf(label, font, labelSize, inner, raw ? 2 : 3) * labelSize * 1.08 + (raw ? size * 0.12 : 0) : 0);
  const valueRow = raw ? (
    <div
      style={{
        display: 'flex',
        alignItems: 'baseline',
        justifyContent: 'center',
        gap: size * 0.08,
        fontFamily: fontStack(font),
        fontWeight: weight,
        color: style.color,
        lineHeight: 1,
        fontVariantNumeric: 'tabular-nums',
        transform: `scale(${scale})`,
        opacity: counted.counting ? 1 : Math.min(1, pop * 1.4),
        whiteSpace: 'nowrap',
      }}
    >
      {prefix ? <span style={affix}>{prefix}</span> : null}
      <span style={{ fontSize: size, textShadow: `0 ${u * 0.008}px ${u * 0.03}px rgba(0,0,0,0.35)` }}>{p ? counted.text : raw}</span>
      {suffix ? <span style={affix}>{suffix}</span> : null}
    </div>
  ) : null;
  return (
    <Panel style={style} scene={scene} lang={lang} pad={pad} band={band} estimate={estimate}>
      {valueRow}
      {label ? (
        <div
          style={{
            marginTop: raw ? size * 0.12 : 0,
            fontFamily: fontStack(font),
            fontWeight: weight,
            fontSize: labelSize,
            lineHeight: 1.08,
            letterSpacing: '0.03em',
            color: withAlpha(style.color, 0.92),
            textWrap: 'balance',
          }}
        >
          {label}
        </div>
      ) : null}
    </Panel>
  );
};

type CompareRow = TimelineCardItem & { parsed: ParsedValue | null; bar: BarValue | null };

/** Barras só fazem sentido entre números da mesma unidade e moeda ("2 MIN" × "30 S" vira 120 × 30). */
const sameScale = (rows: CompareRow[]) =>
  rows.every((r) => r.bar && r.bar.amount >= 0 && r.bar.unit === rows[0].bar!.unit && r.bar.currency === rows[0].bar!.currency);

/** Medidas das barras (as mesmas no desenho e na altura prevista do painel). */
const barsMetrics = (rows: CompareRow[], font: string, inner: number, u: number, k: number) => {
  const valueSize = Math.min(...rows.map((r) => fitSize(r.value, font, inner * 0.5, u * 0.095 * k, u * 0.05 * k)));
  const labelSize = Math.min(...rows.map((r) => fitSize(r.label, font, inner * 0.48, u * 0.055 * k, u * 0.034 * k)));
  const barH = u * 0.04 * k;
  const gap = u * 0.045 * k;
  const height = rows.length * (Math.max(valueSize, labelSize) + u * 0.016 * k + barH) + gap * (rows.length - 1);
  return { valueSize, labelSize, barH, gap, height };
};

/** Medidas dos blocos de texto (no vertical, um embaixo do outro; no horizontal, lado a lado). */
const tilesMetrics = (rows: CompareRow[], font: string, inner: number, u: number, k: number, portrait: boolean) => {
  const across = !portrait;
  const gap = u * (rows.length === 2 ? 0.07 : 0.03) * k;
  const tileW = across ? (inner - gap * (rows.length - 1)) / rows.length : inner;
  const tilePad = u * 0.03 * k;
  // No vertical com 3 blocos, o valor grande não deixa o painel caber entre a faixa do topo e a legenda.
  const valueMax = u * (portrait && rows.length >= 3 ? 0.1 : 0.13) * k;
  const valueSize = Math.min(...rows.map((r) => fitPreferOne(r.value, font, tileW - tilePad * 2, valueMax, u * 0.05 * k, 2, u * 0.08)));
  const labelSize = Math.min(...rows.map((r) => fitLines(r.label, font, tileW - tilePad * 2, u * 0.05 * k, u * 0.032 * k, 2)));
  const tileH = rows.map(
    (r) =>
      tilePad * 2 +
      (r.label ? linesOf(r.label, font, labelSize, tileW - tilePad * 2, 2) * labelSize * 1.08 + u * 0.012 : 0) +
      linesOf(r.value, font, valueSize, tileW - tilePad * 2, 2) * valueSize,
  );
  const height = across ? Math.max(...tileH) : tileH.reduce((a, b) => a + b, 0) + gap * (rows.length - 1);
  return { across, gap, tileW, tilePad, valueSize, labelSize, height };
};

/** Comparação de 2 ou 3 itens: barras proporcionais quando todos são números da mesma unidade; senão, blocos. */
export const CompareCard: React.FC<CardViewProps> = ({ scene, style, lang, band }) => {
  const { portrait, u, panelW } = useLayout();
  const card = scene.card ?? {};
  const rows: CompareRow[] = (card.items ?? [])
    .filter((it) => (it.label ?? '').trim() || (it.value ?? '').trim())
    .slice(0, 3)
    .map((it) => {
      // A grandeza sai do valor original: em maiúsculas, "100 m" (metros) viraria "100 M" (milhões).
      const original = (it.value ?? '').trim();
      const value = upper(original, lang);
      return { label: upper((it.label ?? '').trim(), lang), value, parsed: parseValue(value), bar: barValue(original) };
    });
  const title = upper((card.label ?? '').trim(), lang);
  const highlight = typeof card.highlight === 'number' && card.highlight >= 0 && card.highlight < rows.length ? card.highlight : null;
  const numeric = rows.length >= 2 && sameScale(rows) && !rows.every((r) => r.parsed && isYear(r.parsed));
  // No vertical, com só 2 itens, sobra altura: tudo cresce um pouco.
  const k = portrait ? (rows.length <= 2 ? 1.2 : 1) : 0.74;
  const pad = u * 0.055 * Math.min(k, 1);
  const inner = panelW - pad * 2;
  const font = style.font;
  const weight = cardWeight(font);
  if (!rows.length) {
    return null;
  }
  const titleSize = fitLines(title, font, inner, u * 0.06 * k, u * 0.036, 2);
  const body = numeric ? barsMetrics(rows, font, inner, u, k).height : tilesMetrics(rows, font, inner, u, k, portrait).height;
  const estimate = body + (title ? linesOf(title, font, titleSize, inner, 2) * titleSize * 1.08 + pad * 0.6 : 0);
  return (
    <Panel style={style} scene={scene} lang={lang} pad={pad} band={band} estimate={estimate}>
      {title ? (
        <div
          style={{
            fontFamily: fontStack(font),
            fontWeight: weight,
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
      {numeric ? (
        <CompareBars rows={rows} highlight={highlight} style={style} inner={inner} k={k} />
      ) : (
        <CompareTiles rows={rows} highlight={highlight} style={style} inner={inner} k={k} />
      )}
    </Panel>
  );
};

const CompareBars: React.FC<{ rows: CompareRow[]; highlight: number | null; style: TextCardStyle; inner: number; k: number }> = ({
  rows,
  highlight,
  style,
  inner,
  k,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const { u } = useLayout();
  const max = Math.max(...rows.map((r) => r.bar?.amount ?? 0), 1e-9);
  const font = style.font;
  const weight = cardWeight(font);
  const { valueSize, labelSize, barH, gap } = barsMetrics(rows, font, inner, u, k);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap }}>
      {rows.map((row, i) => {
        const delay = 4 + i * 6;
        const grow = spring({ frame: frame - delay, fps, config: { damping: 200 }, durationInFrames: Math.round(fps * 0.8) });
        const share = Math.max(0.03, (row.bar?.amount ?? 0) / max);
        // Sem destaque, todas as barras ficam na cor de destaque.
        const on = highlight == null || highlight === i;
        const fill = on ? style.accent : withAlpha(style.color, 0.42);
        const shown = row.parsed && !isYear(row.parsed) ? `${row.parsed.prefix}${formatValue(row.parsed.num * grow, row.parsed)}${row.parsed.suffix}` : row.value;
        return (
          <div key={i} style={{ opacity: interpolate(frame - delay, [0, 6], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' }) }}>
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'baseline',
                gap: u * 0.02,
                fontFamily: fontStack(font),
                fontWeight: weight,
                lineHeight: 1,
                marginBottom: u * 0.016 * k,
              }}
            >
              <span style={{ fontSize: labelSize, letterSpacing: '0.03em', color: withAlpha(style.color, on ? 0.95 : 0.7), textAlign: 'left' }}>{row.label}</span>
              <span style={{ fontSize: valueSize, color: highlight === i ? style.accent : style.color, fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }}>
                {shown}
              </span>
            </div>
            <div style={{ height: barH, borderRadius: barH / 2, backgroundColor: withAlpha(style.color, 0.1), overflow: 'hidden' }}>
              <div style={{ height: '100%', width: `${100 * share * grow}%`, borderRadius: barH / 2, backgroundColor: fill }} />
            </div>
          </div>
        );
      })}
    </div>
  );
};

const CompareTiles: React.FC<{ rows: CompareRow[]; highlight: number | null; style: TextCardStyle; inner: number; k: number }> = ({
  rows,
  highlight,
  style,
  inner,
  k,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const { portrait, u } = useLayout();
  const font = style.font;
  const weight = cardWeight(font);
  const { across, gap, tilePad, valueSize, labelSize } = tilesMetrics(rows, font, inner, u, k, portrait);
  const vs = rows.length === 2;
  return (
    <div style={{ display: 'flex', flexDirection: across ? 'row' : 'column', alignItems: 'stretch' }}>
      {rows.map((row, i) => {
        const delay = 4 + i * 6;
        const s = spring({ frame: frame - delay, fps, config: { damping: 13, stiffness: 150, mass: 0.6 } });
        const on = highlight === i;
        return (
          <React.Fragment key={i}>
            {i > 0 ? (
              <div style={{ position: 'relative', flex: `0 0 ${gap}px`, zIndex: 1 }}>
                {vs ? <VsBadge style={style} size={Math.min(u * 0.11 * k, gap * 1.9)} across={across} /> : null}
              </div>
            ) : null}
            <div
              style={{
                flex: 1,
                padding: `${tilePad}px ${tilePad}px`,
                borderRadius: u * 0.02,
                backgroundColor: on ? withAlpha(style.accent, 0.16) : withAlpha(style.color, 0.06),
                boxShadow: on ? `inset 0 0 0 ${Math.max(2, u * 0.004)}px ${style.accent}` : undefined,
                transform: `scale(${interpolate(s, [0, 1], [0.85, 1])})`,
                opacity: Math.min(1, s * 1.3),
                display: 'flex',
                flexDirection: 'column',
                // Lado a lado, os valores ficam alinhados embaixo mesmo com rótulos de alturas diferentes.
                justifyContent: across ? 'space-between' : 'center',
                fontFamily: fontStack(font),
                fontWeight: weight,
              }}
            >
              {row.label ? (
                <div style={{ fontSize: labelSize, lineHeight: 1.08, letterSpacing: '0.03em', color: withAlpha(style.color, 0.72), marginBottom: u * 0.012, textWrap: 'balance' }}>
                  {row.label}
                </div>
              ) : null}
              <div style={{ fontSize: valueSize, lineHeight: 1, color: on ? style.accent : style.color, textWrap: 'balance' }}>{row.value}</div>
            </div>
          </React.Fragment>
        );
      })}
    </div>
  );
};

const VsBadge: React.FC<{ style: TextCardStyle; size: number; across: boolean }> = ({ style, size, across }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame: frame - 12, fps, config: { damping: 10, stiffness: 180, mass: 0.5 } });
  return (
    <div
      style={{
        position: 'absolute',
        left: '50%',
        top: '50%',
        width: size,
        height: size,
        marginLeft: -size / 2,
        marginTop: -size / 2,
        borderRadius: '50%',
        backgroundColor: style.accent,
        color: style.background,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontFamily: fontStack(style.font),
        fontWeight: cardWeight(style.font),
        fontSize: size * 0.42,
        lineHeight: 1,
        boxShadow: `0 0 0 ${size * 0.08}px ${withAlpha(style.background, 0.8)}`,
        transform: `scale(${s}) rotate(${across ? 0 : -8}deg)`,
      }}
    >
      VS
    </div>
  );
};

// ---------------------------------------------------------------------------------------------
// Sobreposições (qualquer tipo de cena)

/** Faixa no topo (~10% da altura): fundo na cor de destaque, texto na cor de fundo, desce ao entrar. */
export const Headline: React.FC<{ text: string; style: TextCardStyle; lang: string }> = ({ text, style, lang }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const { width, height, u } = useLayout();
  const s = spring({ frame, fps, config: { damping: 16, stiffness: 170, mass: 0.6 } });
  const { label, maxW, padX, size } = headlineBox(text, style, lang, width, height);
  return (
    <div
      style={{
        position: 'absolute',
        left: 0,
        right: 0,
        top: height * 0.1,
        display: 'flex',
        justifyContent: 'center',
        transform: `translateY(calc(-50% + ${interpolate(s, [0, 1], [-height * 0.08, 0])}px))`,
        opacity: Math.min(1, s * 1.5),
      }}
    >
      <div
        style={{
          maxWidth: maxW,
          padding: `${size * 0.2}px ${padX}px ${size * 0.14}px`,
          backgroundColor: style.accent,
          color: style.background,
          borderRadius: u * 0.012,
          fontFamily: fontStack(style.font),
          fontWeight: cardWeight(style.font),
          fontSize: size,
          lineHeight: 1.05,
          letterSpacing: '0.02em',
          textAlign: 'center',
          textWrap: 'balance',
          boxShadow: `0 ${u * 0.012}px ${u * 0.04}px rgba(0,0,0,0.4)`,
        }}
      >
        {label}
      </div>
    </div>
  );
};

/** Carimbo girado −10° com borda na cor de destaque; entra com mola de escala 2,2 → 1. */
export const Stamp: React.FC<{
  text: string;
  style: TextCardStyle;
  lang: string;
  delay?: number;
  anchor?: React.CSSProperties;
  /** Largura máxima (px); sem ela, metade da tela no vertical e 30% no horizontal. */
  maxWidth?: number;
}> = ({ text, style, lang, delay = 6, anchor, maxWidth }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const { width, height, portrait, u } = useLayout();
  const s = spring({ frame: frame - delay, fps, config: { damping: 12, stiffness: 200, mass: 0.7 } });
  if (frame < delay) return null;
  const fitted = stampFit(upper(text.trim(), lang), style.font, maxWidth ?? width * (portrait ? 0.5 : 0.3), u);
  const size = fitted.size;
  const border = Math.max(3, u * 0.007);
  const place: React.CSSProperties = anchor ?? { position: 'absolute', right: width * 0.07, top: height * 0.3 };
  return (
    <div style={{ ...place, transform: `${anchor ? '' : 'translateY(-50%) '}rotate(-10deg) scale(${interpolate(s, [0, 1], [2.2, 1])})`, opacity: Math.min(1, s * 2) }}>
      <div
        style={{
          padding: `${size * 0.16}px ${size * 0.34}px ${size * 0.1}px`,
          border: `${border}px solid ${style.accent}`,
          outline: `${Math.max(1, border * 0.4)}px solid ${style.accent}`,
          outlineOffset: -border * 2.4,
          borderRadius: u * 0.012,
          backgroundColor: withAlpha(style.background, 0.72),
          color: style.accent,
          fontFamily: fontStack(style.font),
          fontWeight: cardWeight(style.font),
          fontSize: size,
          lineHeight: 1,
          letterSpacing: '0.06em',
          whiteSpace: 'pre',
          textAlign: 'center',
          boxShadow: `0 ${u * 0.01}px ${u * 0.035}px rgba(0,0,0,0.45)`,
        }}
      >
        {fitted.lines.join('\n')}
      </div>
    </div>
  );
};

/** "Fonte: X" pequeno, à esquerda; `y` é a altura do centro em fração da tela. */
export const SourceChip: React.FC<{ source: string; style: TextCardStyle; lang: string; y: number }> = ({ source, style, lang, y }) => {
  const frame = useCurrentFrame();
  const { width, height, u } = useLayout();
  const opacity = interpolate(frame, [8, 16], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  const size = Math.max(14, u * 0.028);
  return (
    <div
      style={{
        position: 'absolute',
        left: width * 0.06,
        top: height * y,
        transform: 'translateY(-50%)',
        maxWidth: width * 0.7,
        padding: `${size * 0.3}px ${size * 0.6}px`,
        borderRadius: size * 0.4,
        borderLeft: `${Math.max(3, size * 0.18)}px solid ${style.accent}`,
        backgroundColor: 'rgba(0,0,0,0.55)',
        color: '#FFFFFF',
        fontFamily: fontStack('Noto Sans'),
        fontWeight: 600,
        fontSize: size,
        lineHeight: 1.2,
        letterSpacing: '0.01em',
        opacity,
        whiteSpace: 'nowrap',
        overflow: 'hidden',
        textOverflow: 'ellipsis',
      }}
    >
      {sourceText(source.trim(), lang)}
    </div>
  );
};

// ---------------------------------------------------------------------------------------------
// Fundo animado (cena sem imagem)

/** Gradientes radiais do fundo e do destaque do estilo, derivando devagar, com grade sutil. */
export const MotionBackground: React.FC<{ style: TextCardStyle; seed: number }> = ({ style, seed }) => {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const t = frame / fps;
  const r = (key: string) => random(`${seed}-mb-${key}`) * Math.PI * 2;
  const x1 = 28 + 18 * Math.sin(t * 0.42 + r('a'));
  const y1 = 30 + 10 * Math.cos(t * 0.33 + r('b'));
  const x2 = 72 + 16 * Math.cos(t * 0.37 + r('c'));
  const y2 = 74 + 10 * Math.sin(t * 0.29 + r('d'));
  const cell = Math.round(Math.min(width, height) * 0.085);
  const drift = (t * cell * 0.18) % cell;
  const deep = mix(style.background, '#000000', 0.55);
  return (
    <AbsoluteFill style={{ backgroundColor: deep }}>
      <AbsoluteFill
        style={{
          background: [
            `radial-gradient(circle at ${x1}% ${y1}%, ${withAlpha(style.accent, 0.42)} 0%, ${withAlpha(style.accent, 0)} 48%)`,
            `radial-gradient(circle at ${x2}% ${y2}%, ${withAlpha(mix(style.accent, style.background, 0.45), 0.5)} 0%, ${withAlpha(style.background, 0)} 52%)`,
            `linear-gradient(165deg, ${style.background} 0%, ${deep} 100%)`,
          ].join(', '),
        }}
      />
      <AbsoluteFill
        style={{
          backgroundImage: `linear-gradient(${withAlpha(style.color, 0.06)} 1px, transparent 1px), linear-gradient(90deg, ${withAlpha(style.color, 0.06)} 1px, transparent 1px)`,
          backgroundSize: `${cell}px ${cell}px`,
          backgroundPosition: `${drift}px ${drift * 0.6}px`,
          WebkitMaskImage: 'radial-gradient(ellipse at center, #000 30%, transparent 85%)',
          maskImage: 'radial-gradient(ellipse at center, #000 30%, transparent 85%)',
        }}
      />
    </AbsoluteFill>
  );
};
