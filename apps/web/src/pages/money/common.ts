// Peças comuns da página Dinheiro.
import type { QueryClient } from '@tanstack/react-query';

/** Chaves das consultas da página (todas começam com "money" e o id do canal). */
export const moneyKeys = {
  config: (cid: string) => ['money', cid, 'config'] as const,
  offers: (cid: string) => ['money', cid, 'offers'] as const,
  revenue: (cid: string) => ['money', cid, 'revenue'] as const,
};

/** Recarrega tudo do canal (as ofertas mostram o link, que depende da configuração). */
export const refreshMoney = (qc: QueryClient, cid: string) => qc.invalidateQueries({ queryKey: ['money', cid] });

export const formatAmount = (value: number | null | undefined, currency: string) => {
  if (value == null) return '—';
  try {
    return value.toLocaleString('pt-BR', { style: 'currency', currency });
  } catch {
    return `${value.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${currency}`;
  }
};

export const formatCount = (value: number | null | undefined) => (value == null ? '—' : value.toLocaleString('pt-BR'));

/** "2026-11" → "nov. de 2026". */
export const monthLabel = (period: string) => {
  const [y, m] = period.split('-').map(Number);
  if (!y || !m) return period;
  return new Date(y, m - 1, 1).toLocaleDateString('pt-BR', { month: 'short', year: 'numeric' });
};

/** Número digitado no jeito brasileiro (ponto de milhar, vírgula nos centavos) ou null quando vazio; NaN quando
 * inválido ou ambíguo. `integer` (cliques, pedidos): todo ponto é de milhar ("1.234" = 1234). Valores: "1.500" é mil
 * e quinhentos, "12.5" e "12.50" são centavos; "1.2345" é ambíguo (peça a vírgula). */
export const parseNumber = (text: string, integer = false): number | null => {
  const t = text.trim().replace(/\s/g, '');
  if (t === '') return null;
  if (t.includes(',')) {
    if (t.indexOf(',') !== t.lastIndexOf(',') || !/^-?\d{1,3}(\.\d{3})*,\d*$|^-?\d*,\d*$/.test(t)) return NaN;
    const n = Number(t.replace(/\./g, '').replace(',', '.'));
    return integer && !Number.isInteger(n) ? NaN : n;
  }
  if (integer || /^-?\d{1,3}(\.\d{3})+$/.test(t)) {
    if (t.includes('.') && !/^-?\d{1,3}(\.\d{3})+$/.test(t)) return NaN;
    return Number(t.replace(/\./g, ''));
  }
  return /^-?\d+(\.\d{1,2})?$/.test(t) ? Number(t) : NaN;
};
