// Contas do painel de Shorts: trechos a partir da seleção e duração do Short (mesma conta do backend).
import type { ShortKind, ShortLimits, ShortRange, ShortsOfProject } from '../../../../apiShorts';

/** Trechos contínuos (na ordem do vídeo) a partir dos índices selecionados. */
export const rangesFrom = (data: ShortsOfProject, selected: Set<number>): ShortRange[] => {
  const idx = [...selected].sort((a, b) => a - b);
  const out: ShortRange[] = [];
  let start = -1;
  let prev = -2;
  for (const i of idx) {
    if (i !== prev + 1) {
      if (start >= 0) out.push([data.segments[start].id, data.segments[prev].id]);
      start = i;
    }
    prev = i;
  }
  if (start >= 0) out.push([data.segments[start].id, data.segments[prev].id]);
  return out;
};

/** Índices dos segmentos de uma lista de ids. */
export const indexesOf = (data: ShortsOfProject, ids: string[]) => {
  const pos = new Map(data.segments.map((s) => [s.id, s.index]));
  return new Set(ids.map((id) => pos.get(id)).filter((i): i is number => i !== undefined));
};

/** Duração do Short: narrações + pausas entre elas + entrada e saída do formato Short. */
export const shortDuration = (data: ShortsOfProject, selected: Set<number>) => {
  if (!selected.size) return 0;
  let total = 0;
  for (const i of selected) total += data.segments[i]?.duration_s ?? 0;
  return total + data.gap_s * (selected.size - 1) + data.lead_in_s + data.tail_out_s;
};

export const ceilingOf = (limits: ShortLimits, tipo: ShortKind) =>
  tipo === 'lista' || tipo === 'iceberg' ? limits.max_list_s : limits.max_s;

/** Tom do contador de segundos: dentro do alvo, até o teto ou passou. */
export const durationTone = (seconds: number, limits: ShortLimits, tipo: ShortKind): 'good' | 'warn' | 'bad' => {
  if (seconds > ceilingOf(limits, tipo)) return 'bad';
  if (tipo !== 'normal') return 'good';
  return seconds >= limits.target[0] && seconds <= limits.target[1] ? 'good' : 'warn';
};

export const fmtSeconds = (s: number) => `${s.toLocaleString('pt-BR', { maximumFractionDigits: 1, minimumFractionDigits: 1 })} s`;

export const TIPO_OPTIONS: { id: ShortKind; label: string }[] = [
  { id: 'normal', label: 'Normal' },
  { id: 'lista', label: 'Lista' },
  { id: 'iceberg', label: 'Iceberg' },
];
