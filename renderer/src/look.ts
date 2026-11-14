// Gradação de cor e ajustes por cena (só CSS, então funciona igual no render e na prévia).
import type { EffectSpec, SceneLook } from './types';

type Grade = { filter: (a: number) => string; tint?: { color: (a: number) => string; blend: string } };

export const GRADES: Record<string, Grade> = {
  neutral: { filter: () => '' },
  'cold-teal-dark': {
    filter: (a) => `contrast(${1 + 0.14 * a}) saturate(${1 - 0.18 * a}) brightness(${1 - 0.1 * a})`,
    tint: { color: (a) => `rgba(0, 95, 115, ${0.38 * a})`, blend: 'soft-light' },
  },
  'warm-sepia': {
    filter: (a) => `sepia(${0.5 * a}) contrast(${1 + 0.06 * a}) saturate(${1 + 0.05 * a})`,
    tint: { color: (a) => `rgba(150, 95, 30, ${0.25 * a})`, blend: 'soft-light' },
  },
  noir: { filter: (a) => `grayscale(${a}) contrast(${1 + 0.38 * a}) brightness(${1 - 0.05 * a})` },
  'bleach-bypass': { filter: (a) => `saturate(${1 - 0.45 * a}) contrast(${1 + 0.3 * a})` },
  'vhs-faded': {
    filter: (a) => `saturate(${1 - 0.25 * a}) contrast(${1 - 0.08 * a}) brightness(${1 + 0.04 * a})`,
    tint: { color: (a) => `rgba(170, 70, 170, ${0.16 * a})`, blend: 'screen' },
  },
  'blood-red': {
    filter: (a) => `contrast(${1 + 0.2 * a}) saturate(${1 - 0.1 * a})`,
    tint: { color: (a) => `rgba(150, 0, 0, ${0.36 * a})`, blend: 'multiply' },
  },
  'night-green': {
    filter: (a) => `grayscale(${0.6 * a}) contrast(${1 + 0.15 * a})`,
    tint: { color: (a) => `rgba(40, 150, 70, ${0.34 * a})`, blend: 'multiply' },
  },
};

export const activeGrade = (effects: EffectSpec[], look: SceneLook) => {
  const effect = effects.find((e) => e.type === 'grade');
  const name = look.grade ?? effect?.preset ?? 'neutral';
  const amount = effect?.amount ?? (look.grade ? 0.7 : 0);
  return { grade: GRADES[name] ?? GRADES.neutral, amount };
};

/** Filtro CSS final da imagem: gradação do estilo + ajustes da cena + efeitos simples. */
export const imageFilter = (effects: EffectSpec[], look: SceneLook) => {
  const parts: string[] = [];
  const { grade, amount } = activeGrade(effects, look);
  const g = grade.filter(amount);
  if (g) parts.push(g);
  const sepia = effects.find((e) => e.type === 'sepia');
  if (sepia) parts.push(`sepia(${0.8 * sepia.amount})`);
  if (look.brightness) parts.push(`brightness(${Math.max(0.1, 1 + look.brightness * 0.6)})`);
  if (look.contrast) parts.push(`contrast(${Math.max(0.2, 1 + look.contrast * 0.6)})`);
  if (look.saturation) parts.push(`saturate(${Math.max(0, 1 + look.saturation)})`);
  if (look.warmth > 0) parts.push(`sepia(${look.warmth * 0.35}) saturate(${1 + look.warmth * 0.2})`);
  if (look.warmth < 0) parts.push(`hue-rotate(${look.warmth * -18}deg) saturate(${1 + look.warmth * 0.25})`);
  if (look.blur) parts.push(`blur(${look.blur * 8}px)`);
  return parts.join(' ');
};

export const hasEffect = (effects: EffectSpec[], type: string) => effects.find((e) => e.type === type);
