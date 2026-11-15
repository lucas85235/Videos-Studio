/* Formatos do radar (mesmos do painel do canais). */

/** Número curto: "1,2 mi", "350 mil", "999". */
export const fmtNum = (v: number | null | undefined): string => {
  if (v == null) return '—';
  if (v >= 1e6) {
    const m = v / 1e6;
    return `${m.toLocaleString('pt-BR', { maximumSignificantDigits: m >= 10 ? 4 : 3 })} mi`;
  }
  if (v >= 1e3) return `${(v / 1e3).toLocaleString('pt-BR', { maximumSignificantDigits: 3 })} mil`;
  return Math.round(v).toLocaleString('pt-BR');
};

/** Idade do canal em meses → "12 dias", "1 mês", "8 meses". */
export const ageText = (m: number | null | undefined): string => {
  if (m == null) return '—';
  if (m < 1) {
    const d = Math.max(1, Math.round(m * 30.4));
    return `${d} ${d === 1 ? 'dia' : 'dias'}`;
  }
  const r = Math.round(m);
  return `${r} ${r === 1 ? 'mês' : 'meses'}`;
};

/** Dias desde a publicação → "hoje", "há 3 dias", "há 2 sem.", "há 1 mês". */
export const agoText = (d: number | null | undefined): string => {
  if (d == null) return '—';
  if (d === 0) return 'hoje';
  if (d < 7) return `há ${d} ${d === 1 ? 'dia' : 'dias'}`;
  if (d < 30) return `há ${Math.round(d / 7)} sem.`;
  if (d < 365) {
    const m = Math.round(d / 30);
    return `há ${m} ${m === 1 ? 'mês' : 'meses'}`;
  }
  const y = Math.round(d / 365);
  return `há ${y} ${y === 1 ? 'ano' : 'anos'}`;
};

export const thumbUrl = (id: string, size: 'mq' | 'hq' = 'mq') => `https://i.ytimg.com/vi/${id}/${size}default.jpg`;
export const watchUrl = (id: string) => `https://www.youtube.com/watch?v=${id}`;

export const decimal = (v: number, digits = 1) => v.toFixed(digits).replace('.', ',');

/** Razão views ÷ inscritos (mínimo de 1.000 inscritos), como no painel. */
export const ratioText = (r: number) => (r >= 10 ? String(Math.round(r)) : decimal(r));

export const VERDICT_TONE = { Modelar: 'good', Observar: 'info', 'Estudar o viral': 'warn' } as const;
export const VERDICT_RANK: Record<string, number> = { Modelar: 0, Observar: 1, 'Estudar o viral': 2 };
