/* Peças comuns da pesquisa de vídeos: datas, selos da checagem, lacunas e filtros de formato e mercado. */
import { TriangleAlert } from 'lucide-react';
import React from 'react';
import {
  RESEARCH_FORMAT_LABELS,
  RESEARCH_MARKET_SHORT,
  type ResearchCheck,
  type ResearchFormat,
  type ResearchMarket,
} from '../../apiResearch';
import { Badge, type BadgeTone, SegmentedControl } from '../../ui';
import { decimal } from '../radar/format';

/** "AAAA-MM-DD" → "sex., 21 de nov." (sem o fuso empurrar o dia). */
export const dayText = (iso: string | null | undefined, withYear = false): string => {
  if (!iso) return 'sem data';
  const d = new Date(`${iso.slice(0, 10)}T12:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('pt-BR', { weekday: 'short', day: 'numeric', month: 'short', ...(withYear ? { year: 'numeric' } : {}) });
};

/** Data e hora de uma rodada: "17/11/2026 08:12". */
export const runDateText = (iso: string | null | undefined): string => {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' });
};

const VIDEO_ID_RE = /(?:youtu\.be\/|[?&]v=|\/shorts\/|\/live\/)([0-9A-Za-z_-]{11})/;

/** Id do vídeo do YouTube num link (para a miniatura), ou null. */
export const youtubeIdOf = (url: string | null | undefined): string | null => VIDEO_ID_RE.exec(url ?? '')?.[1] ?? null;

/** Multiplicador "3,2×". */
export const multText = (m: number | null | undefined) => (m == null ? '—' : `${decimal(m)}×`);

/** Nota de 0 a 10 com tom. */
export const scoreTone = (s: number): BadgeTone => (s >= 8 ? 'good' : s >= 6 ? 'info' : 'neutral');

const CHECK_BADGE: Record<ResearchCheck['status'], { tone: BadgeTone; label: string; title: string }> = {
  nova: { tone: 'good', label: 'Nova', title: 'Nenhum vídeo feito nem ideia parecida' },
  parecida: { tone: 'warn', label: 'Parecida', title: 'Parecida com um vídeo já feito (salva com aviso no risco)' },
  repetida: { tone: 'bad', label: 'Repetida', title: 'Quase igual a um vídeo já feito: não foi salva' },
  ja_no_banco: { tone: 'info', label: 'Já em Ideias', title: 'Uma ideia com esse título (ou quase igual) já estava no banco' },
};

/** Selo da checagem contra os vídeos feitos e o banco de ideias. */
export const CheckBadge: React.FC<{ check: ResearchCheck | undefined }> = ({ check }) => {
  if (!check) return null;
  const base = CHECK_BADGE[check.status] ?? CHECK_BADGE.nova;
  // Salva à mão depois da checagem ("Salvar mesmo assim"): o selo diz que ela está no banco.
  const b =
    check.saved && check.status === 'repetida'
      ? { tone: 'warn' as BadgeTone, label: 'Repetida, salva à mão', title: 'Quase igual a um vídeo já feito: você salvou mesmo assim' }
      : base;
  const sim = check.similar_to?.title;
  return (
    <Badge tone={b.tone} title={sim ? `${b.title}: "${sim}"` : b.title}>
      {b.label}
      {sim && check.status !== 'nova' ? `: ${sim.length > 40 ? `${sim.slice(0, 40)}…` : sim}` : ''}
    </Badge>
  );
};

/** O que a rodada não conseguiu medir ou verificar. */
export const GapsBox: React.FC<{ gaps: string[]; title?: string }> = ({ gaps, title = 'Lacunas desta rodada' }) => {
  if (!gaps.length) return null;
  return (
    <div className="rounded-lg border border-amber-300/30 bg-amber-300/5 p-3" role="note" aria-label={title}>
      <div className="mb-1.5 flex items-center gap-2 text-sm font-medium text-amber-200">
        <TriangleAlert size={15} aria-hidden="true" />
        {title}
      </div>
      <ul className="flex list-disc flex-col gap-1 pl-5 text-[13px] text-muted">
        {gaps.map((g) => (
          <li key={g}>{g}</li>
        ))}
      </ul>
    </div>
  );
};

export type FormatFilter = 'all' | ResearchFormat;

/** Filtro Longos/Shorts: longos e Shorts são públicos diferentes, então a tela separa os dois. */
export const FormatFilterControl: React.FC<{
  value: FormatFilter;
  onChange: (v: FormatFilter) => void;
  counts: Record<ResearchFormat, number>;
  withAll?: boolean;
}> = ({ value, onChange, counts, withAll = true }) => (
  <SegmentedControl
    label="Formato"
    size="sm"
    value={value}
    onChange={onChange}
    options={[
      ...(withAll ? [{ id: 'all' as const, label: 'Todos', count: counts.long + counts.short }] : []),
      { id: 'long' as const, label: 'Longos', count: counts.long },
      { id: 'short' as const, label: 'Shorts', count: counts.short },
    ]}
  />
);

export type MarketFilter = 'all' | ResearchMarket;

export const MarketFilterControl: React.FC<{ value: MarketFilter; onChange: (v: MarketFilter) => void; counts: Record<ResearchMarket, number> }> = ({
  value,
  onChange,
  counts,
}) => (
  <SegmentedControl
    label="Mercado"
    size="sm"
    value={value}
    onChange={onChange}
    options={[
      { id: 'all', label: 'Todos', count: counts.br + counts.us },
      { id: 'br', label: RESEARCH_MARKET_SHORT.br, count: counts.br },
      { id: 'us', label: RESEARCH_MARKET_SHORT.us, count: counts.us },
    ]}
  />
);

export const countFormats = (items: { format: ResearchFormat }[]): Record<ResearchFormat, number> => ({
  long: items.filter((i) => i.format === 'long').length,
  short: items.filter((i) => i.format === 'short').length,
});

export const formatLabel = (f: ResearchFormat) => RESEARCH_FORMAT_LABELS[f] ?? f;
