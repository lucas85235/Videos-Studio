import React from 'react';
import { interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { fontStack } from '../fonts';
import type { CaptionLine, CaptionStyle } from '../types';
import { charEm, mix } from './DataCards';

/** `topOverride`: altura do centro da linha em fração da tela (ex.: 0.76 abaixo dos cartões); `lang`: idioma do vídeo
 *  (palavras que não fecham a 1ª fileira). */
type Props = { lines: CaptionLine[]; style: CaptionStyle; topOverride?: number; lang?: string };

export const captionTop = (position: CaptionStyle['position']) => (position === 'top' ? 0.16 : position === 'center' ? 0.56 : 0.8);

// Zonas seguras do vertical (TikTok, Reels, Shorts): os 20% de baixo ficam com o nome do canal e a descrição, e a
// coluna de botões (curtir, comentar, compartilhar) ocupa a direita da tela.
/** Borda de baixo da legenda no vertical, em fração da altura. */
export const SAFE_BOTTOM = 0.79;
const SIDE = { left: 0.08, right: 0.13 };
const SIDE_WIDE = { left: 0.07, right: 0.07 };

/** Meia altura do bloco de legenda (até 2 linhas), em fração da altura da tela. */
export const captionHalfHeight = (style: CaptionStyle, width: number, height: number) =>
  style.style === 'none' ? 0 : (style.size * (width / 1080) * 1.12) / height;

/** Centro da legenda em fração da altura: a posição do estilo (ou `override`); no vertical, sem passar da zona segura de baixo. */
export const captionCenter = (style: CaptionStyle, width: number, height: number, override?: number) => {
  const base = override ?? captionTop(style.position);
  if (height <= width) return base;
  return Math.min(base, SAFE_BOTTOM - captionHalfHeight(style, width, height));
};

const clean = (text: string, uppercase: boolean, dropPunctuation: boolean) => {
  let t = dropPunctuation ? text.replace(/[.,;:!?…"“”]+$/u, '') : text;
  if (uppercase) t = t.toLocaleUpperCase();
  return t;
};

// Palavras que não fecham a 1ª fileira ("DE / VISUALIZAÇÕES", "RECORDE NO / YOUTUBE"): as de ligação do
// FUNCTION_WORDS do backend (studio/core/timeline.py), que já não deixa a linha inteira terminar nelas. Normalizadas
// como lá: minúsculas, sem acento nem pontuação ("é" vira "e", "à" vira "a"). Pronome sujeito e advérbio ("QUE ELE /
// JÁ QUEBROU") ficam de fora: fechar a fileira neles soa natural, e bloqueá-los deixava a linha numa fileira só, miúda.
const FUNCTION_WORDS: Record<string, string> = {
  pt:
    'a o as os ao aos um uma uns umas de da do das dos em no na nos nas num numa naquele naquela nesse nessa neste ' +
    'nesta pelo pela pelos pelas por para pra com sem sob sobre entre ate desde contra e ou mas nem que se como ' +
    'quando onde porque cujo cuja lhe me te seu sua seus suas meu minha nosso nossa esse essa este esta aquele aquela',
  en:
    'a an the of to in on at by for with from into onto and or but nor as if that than so its his their our your ' +
    'my is are was were be been has have had will would can could should very who which whose where when these those',
  es:
    'a al el la los las lo un una unos unas de del en con por para sin sobre entre hasta desde y e o u ni pero que ' +
    'se su sus mi mis tu tus es son fue muy como cuando donde porque le les me te nos',
};
const WEAK: Record<string, Set<string>> = Object.fromEntries(Object.entries(FUNCTION_WORDS).map(([k, v]) => [k, new Set(v.split(' '))]));
/** Idioma sem lista: vale a de todos (um falso positivo só muda a quebra). */
const WEAK_ANY = new Set(Object.values(WEAK).flatMap((s) => [...s]));

// Número não se separa da escala nem se parte no meio ("90 / MILHÕES", "DOIS MIL / E TREZE"): o mesmo cuidado do
// NO_LINE_START e do number_runs do backend.
const SCALE_WORDS = new Set(
  (
    'milhao milhoes bilhao bilhoes trilhao trilhoes cento mil hundred thousand million millions billion billions ' +
    'trillion percent millon millones billon billones ciento'
  ).split(' '),
);
const NUMBER_WORDS = new Set(
  (
    'zero um uma dois duas tres quatro cinco seis sete oito nove dez onze doze treze catorze quatorze quinze dezesseis ' +
    'dezessete dezoito dezenove vinte trinta quarenta cinquenta sessenta setenta oitenta noventa cem duzentos ' +
    'duzentas trezentos quatrocentos quinhentos seiscentos setecentos oitocentos novecentos ' +
    'one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen ' +
    'eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety ' +
    'uno dos cuatro seis siete ocho nueve diez once doce trece veinte treinta cuarenta cincuenta sesenta setenta ochenta noventa cien'
  ).split(' '),
);
const NUMBER_JOINS = new Set(['e', 'and', 'y']);

const normalizeWord = (text: string) =>
  text
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, '');

/** Fileira que termina nesta palavra fica "pendurada" ("O DA / ..."); vírgula ou travessão no fim fecham a frase. */
const weakEnd = (text: string, weak: Set<string>) => !/[,;:.!?…—–]["'”’»)\]]*$/u.test(text) && weak.has(normalizeWord(text));

const numeric = (text: string) => /\d/.test(text) || NUMBER_WORDS.has(normalizeWord(text)) || SCALE_WORDS.has(normalizeWord(text));

/** Quebra antes da palavra `i` que parte um número: escala depois de número ("90 / MILHÕES") ou dentro de um número
 *  por extenso ("DOIS MIL / E TREZE", "VINTE E / TRÊS"). */
const splitsNumber = (texts: string[], i: number) => {
  const before = texts[i - 1];
  const here = normalizeWord(texts[i]);
  if (/[,;:.!?…—–]["'”’»)\]]*$/u.test(before) || !numeric(before)) return false;
  if (SCALE_WORDS.has(here) || numeric(texts[i])) return true;
  return NUMBER_JOINS.has(here) && i + 1 < texts.length && numeric(texts[i + 1]);
};

/** Menor escala (do tamanho que caberia) que a quebra explícita aceita antes de deixar a fileira quebrar sozinha. */
const MIN_ROW_SCALE = 0.65;

/**
 * Fileiras e tamanho da letra de uma linha de legenda: `split` é o índice da 1ª palavra da 2ª fileira (null: uma
 * fileira). Mede pela largura média de letra (`em`, em em) como antes: uma fileira quando cabe (ou quase: encolhe
 * até 88%); senão duas, na quebra em que a fileira mais larga fica menor (letra maior; no empate, fileiras mais
 * parecidas), nunca logo depois de palavra funcional. Sem quebra boa ("DE VISUALIZAÇÕES", "O DA EXPECTATIVA."),
 * fica numa fileira e encolhe; só encolhendo demais (abaixo de `MIN_ROW_SCALE`) aceita a quebra fraca. Com
 * `pick` false (estilo palavra a palavra), só o tamanho, como antes.
 */
export const captionRows = (
  texts: string[],
  { room, base, em, stroke, lang, pick = true }: { room: number; base: number; em: number; stroke: number; lang?: string; pick?: boolean },
): { size: number; split: number | null } => {
  const n = texts.length;
  const longest = Math.max(1, ...texts.map((t) => t.length));
  const chars = (a: number, b: number) => texts.slice(a, b).join(' ').length;
  // Tamanho em que as palavras [a, b) cabem numa fileira (cada uma com o contorno dos dois lados).
  const fit = (a: number, b: number) => (room - stroke * 2 * (b - a)) / (Math.max(1, chars(a, b)) * em);
  const top = Math.min(base, (room - stroke * 2) / (longest * em));
  const one = fit(0, n);
  if (!pick || n < 2 || one >= base * 0.88 || one >= top) return { size: one >= base * 0.88 ? Math.min(top, one) : top, split: null };
  const weak = WEAK[(lang ?? '').slice(0, 2).toLowerCase()] ?? WEAK_ANY;
  const best = (allowWeak: boolean) => {
    let found: { size: number; split: number; skew: number } | null = null;
    for (let i = 1; i < n; i += 1) {
      // Número partido nunca vale, nem como quebra fraca ("VINTE E / TRÊS" também: o "e" entre dois números).
      if (splitsNumber(texts, i) || (i >= 2 && NUMBER_JOINS.has(normalizeWord(texts[i - 1])) && numeric(texts[i]) && numeric(texts[i - 2]))) continue;
      if (!allowWeak && weakEnd(texts[i - 1], weak)) continue;
      const size = Math.min(top, fit(0, i), fit(i, n));
      const skew = Math.abs(chars(0, i) - chars(i, n));
      const bigger = !found || size > found.size + 0.01;
      if (bigger || (found && Math.abs(size - found.size) <= 0.01 && skew < found.skew)) found = { size, split: i, skew };
    }
    return found;
  };
  const strong = best(false);
  // Uma fileira encolhida vence a quebra quando dá letra maior (ou quando não há quebra boa).
  const choice = !strong || one >= strong.size ? { size: one, split: null } : strong;
  if (choice.size >= top * MIN_ROW_SCALE) return { size: choice.size, split: choice.split };
  const any = best(true);
  const fallback = any && any.size > choice.size ? any : choice;
  // Nem assim cabe: não encolhe mais que isso; a fileira quebra sozinha, como antes.
  return { size: Math.max(fallback.size, top * MIN_ROW_SCALE), split: fallback.split };
};

/** Legendas queimadas: karaokê (palavra ativa destacada), palavra a palavra ou linha. */
export const Captions: React.FC<Props> = ({ lines, style, topOverride, lang }) => {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  if (style.style === 'none') return null;
  const index = lines.findIndex((l) => frame >= l.from && frame < l.to);
  const line = lines[index];
  if (!line) return null;
  const scale = width / 1080;
  const side = height > width ? SIDE : SIDE_WIDE;
  const room = width * (1 - side.left - side.right);
  const stroke = style.stroke * scale;
  // Palavra comprida não vaza para a coluna de botões; linha que quase cabe numa fileira encolhe um pouco em vez
  // de quebrar; em duas fileiras, a quebra é escolhida (ver `captionRows`).
  const em = charEm(style.font) * (style.uppercase ? 1.04 : 0.86);
  const texts = line.words.map((w) => w.text);
  const { size: fontSize, split } = captionRows(texts, { room, base: style.size * scale, em, stroke, lang, pick: style.style !== 'word' });
  const top = height * captionCenter(style, width, height, topOverride);
  // Linha que emenda na anterior (mesma frase) entra com menos "pop": linhas curtas não piscam.
  const prev = index > 0 ? lines[index - 1] : undefined;
  const chained = prev !== undefined && line.from - prev.to <= 2;
  const enter = spring({ frame: frame - line.from, fps, config: { damping: 16, stiffness: 220, mass: 0.5 } });
  const lineScale = interpolate(enter, [0, 1], [chained ? 0.95 : 0.86, 1]);
  // Karaokê: a palavra ainda não dita fica numa cor opaca mais apagada; o contorno e a sombra ficam inteiros
  // (opacidade deixaria o fundo aparecer dentro das letras).
  const dimColor = style.dim_color ?? mix(style.color, style.stroke_color, 0.16);
  const baseText: React.CSSProperties = {
    fontFamily: fontStack(style.font),
    fontWeight: style.weight,
    fontSize,
    lineHeight: 1.12,
    color: style.color,
    WebkitTextStroke: stroke ? `${stroke}px ${style.stroke_color}` : undefined,
    paintOrder: 'stroke fill',
    textShadow: `0 ${4 * scale}px ${14 * scale}px rgba(0,0,0,0.55)`,
    letterSpacing: style.uppercase ? '0.01em' : undefined,
  };
  const activeIndex = line.words.findIndex((w, i) => frame >= w.from && (i === line.words.length - 1 || frame < line.words[i + 1].from));
  const last = line.words.length - 1;
  return (
    <div
      style={{
        position: 'absolute',
        left: width * side.left,
        right: width * side.right,
        top,
        transform: `translateY(-50%) scale(${lineScale})`,
        textAlign: 'center',
        display: 'flex',
        flexWrap: 'wrap',
        justifyContent: 'center',
        // O espaço conta o contorno, que vaza metade para cada lado da palavra.
        gap: `0 ${fontSize * 0.3 + stroke}px`,
        padding: style.background ? `${fontSize * 0.2}px ${fontSize * 0.4}px` : undefined,
        backgroundColor: style.background ?? undefined,
        borderRadius: style.background ? fontSize * 0.25 : undefined,
      }}
    >
      {style.style === 'word'
        ? (() => {
            const w = line.words[Math.max(0, activeIndex)];
            const pop = spring({ frame: frame - w.from, fps, config: { damping: 12, stiffness: 260, mass: 0.4 } });
            return (
              <span style={{ ...baseText, color: style.highlight, transform: `scale(${interpolate(pop, [0, 1], [0.7, 1])})`, display: 'inline-block' }}>
                {clean(w.text, style.uppercase, true)}
              </span>
            );
          })()
        : line.words.flatMap((w, i) => {
            const active = i === activeIndex;
            const spoken = activeIndex >= 0 && i <= activeIndex;
            const pop = active ? spring({ frame: frame - w.from, fps, config: { damping: 14, stiffness: 300, mass: 0.4 } }) : 0;
            // Karaokê: a palavra ainda não falada fica apagada; no modo linha todas ficam acesas.
            const dimmed = style.style === 'karaoke' && activeIndex >= 0 && !spoken;
            const word = (
              <span
                key={i}
                style={{
                  ...baseText,
                  color: active ? style.highlight : dimmed ? dimColor : style.color,
                  display: 'inline-block',
                  // O destaque sobe a palavra sem mudar a largura (escala comia o espaço vizinho).
                  transform: active ? `translateY(${-0.06 * fontSize * pop}px)` : undefined,
                }}
              >
                {/* Karaokê: só a pontuação do fim da linha sai; a vírgula do meio fica ("COM LUCIA, A"). */}
                {clean(w.text, style.uppercase, style.style === 'karaoke' && i === last)}
              </span>
            );
            // Quebra escolhida: um item de largura inteira e altura zero fecha a 1ª fileira antes desta palavra.
            return i === split ? [<span key="row" style={{ flexBasis: '100%', height: 0 }} />, word] : [word];
          })}
    </div>
  );
};
