/* Formatos de número da página Desempenho (vírgula decimal, "mil"/"mi" como no canais). */

/** Número curto: 950, "1,2 mil", "3 mil", "1,5 mi"; vazio vira "—". */
export const fmtCount = (v: number | null | undefined): string => {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—';
  if (v >= 1e6) return `${(v / 1e6).toFixed(1).replace('.', ',')} mi`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(1).replace('.', ',').replace(',0', '')} mil`;
  return String(Math.round(v));
};

export const fmtPct = (v: number | null | undefined): string =>
  v === null || v === undefined ? '—' : `${v.toFixed(1).replace('.', ',')}%`;

/** Idade do vídeo: "18 h" até 2 dias, depois "5 dias". */
export const fmtAge = (hours: number | null | undefined): string => {
  if (hours === null || hours === undefined) return '—';
  if (hours < 48) return `${Math.round(hours)} h`;
  return `${Math.round(hours / 24)} dias`;
};

export const fmtDate = (iso: string | null | undefined): string => {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleDateString('pt-BR', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }).replace('.', '');
};

/** Número digitado (aceita vírgula) ou undefined se vazio. NaN = inválido. */
export const parseNumber = (text: string): number | undefined => {
  const raw = text.trim().replace(/\s/g, '');
  // "1.234,5" (milhar com ponto) ou "4,5"/"4.5" (decimal com vírgula ou ponto).
  const t = raw.includes(',') ? raw.replace(/\./g, '').replace(',', '.') : raw;
  if (!t) return undefined;
  return Number(t);
};

/** Contagem digitada (views, likes…): ponto é separador de milhar ("12.500" = 12500), sem casa decimal.
 *  Undefined se vazio; NaN se tiver vírgula, ponto fora do lugar de milhar ou outra coisa que não seja dígito. */
export const parseCount = (text: string): number | undefined => {
  const raw = text.trim().replace(/\s/g, '');
  if (!raw) return undefined;
  if (!/^\d{1,3}(\.\d{3})+$|^\d+$/.test(raw)) return Number.NaN;
  return Number(raw.replace(/\./g, ''));
};

export const todayIso = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};
