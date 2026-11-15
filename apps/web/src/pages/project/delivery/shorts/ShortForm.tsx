// Formulário do Short: contador de segundos com a faixa do alvo, título, gancho, tipo e avisos ao vivo.
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { Scissors, TriangleAlert } from 'lucide-react';
import React, { useEffect, useState } from 'react';
import { shortsApi, type ShortKind, type ShortsOfProject } from '../../../../apiShorts';
import { Badge, Button, errorMessage, Field, Input, SegmentedControl } from '../../../../ui';
import { ceilingOf, durationTone, fmtSeconds, TIPO_OPTIONS } from './model';

export type ShortDraft = { title: string; hook: string; tipo: ShortKind };

/** Valor que só muda depois de `ms` sem mudanças (para não chamar o servidor a cada tecla). */
const useDebounced = <T,>(value: T, ms: number): T => {
  const [out, setOut] = useState(value);
  useEffect(() => {
    const t = window.setTimeout(() => setOut(value), ms);
    return () => window.clearTimeout(t);
  }, [value, ms]);
  return out;
};

const TONE_BAR = { good: 'bg-emerald-400', warn: 'bg-amber-300', bad: 'bg-red-400' } as const;
const TONE_TEXT = { good: 'text-emerald-300', warn: 'text-amber-200', bad: 'text-red-300' } as const;

/** Barra de 0 ao teto + 10 s com a faixa do alvo marcada. */
const DurationMeter: React.FC<{ seconds: number; data: ShortsOfProject; tipo: ShortKind }> = ({ seconds, data, tipo }) => {
  const { limits } = data;
  const ceiling = ceilingOf(limits, tipo);
  const scale = ceiling + 10;
  const pct = (s: number) => `${Math.min(100, (s / scale) * 100)}%`;
  const tone = durationTone(seconds, limits, tipo);
  const target = tipo === 'normal' ? limits.target : [0, ceiling];
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-baseline justify-between gap-2">
        <span className={`font-mono text-lg font-semibold ${seconds ? TONE_TEXT[tone] : 'text-muted'}`} aria-live="polite">
          {fmtSeconds(seconds)}
        </span>
        <span className="text-[12px] text-muted">
          {tipo === 'normal' ? `alvo ${target[0]} a ${target[1]} s · ` : ''}teto {ceiling} s
        </span>
      </div>
      <div className="relative h-2 overflow-hidden rounded-full bg-raised" aria-hidden="true">
        <div className="absolute inset-y-0 bg-emerald-400/20" style={{ left: pct(target[0]), width: `calc(${pct(target[1])} - ${pct(target[0])})` }} />
        <div className={`absolute inset-y-0 left-0 rounded-full ${TONE_BAR[tone]}`} style={{ width: pct(seconds) }} />
        <div className="absolute inset-y-0 w-px bg-red-400/70" style={{ left: pct(ceiling) }} />
      </div>
    </div>
  );
};

export const ShortForm: React.FC<{
  data: ShortsOfProject;
  channelId: string | null;
  seconds: number;
  rangesCount: number;
  draft: ShortDraft;
  onDraft: (d: ShortDraft) => void;
  creating: boolean;
  onCreate: () => void;
  onClear: () => void;
}> = ({ data, channelId, seconds, rangesCount, draft, onDraft, creating, onCreate, onClear }) => {
  // Debounce de uma string (um objeto novo a cada render reiniciaria o tempo sem parar).
  const key = useDebounced(
    JSON.stringify({ title: draft.title.trim(), duration_s: Math.round(seconds * 10) / 10, tipo: draft.tipo }),
    400,
  );
  const check = JSON.parse(key) as { title: string; duration_s: number; tipo: ShortKind };
  const live = useQuery({
    queryKey: ['shorts-check', key, channelId],
    queryFn: () => shortsApi.check({ ...check, channel_id: channelId }),
    enabled: check.duration_s > 0,
    placeholderData: keepPreviousData,
    retry: false,
  });
  const warnings = live.data?.warnings ?? [];
  const finalTitle = live.data?.final_title;
  const canCreate = rangesCount > 0 && draft.title.trim().length > 0 && !live.isError;

  return (
    <div className="flex flex-col gap-3">
      <DurationMeter seconds={seconds} data={data} tipo={draft.tipo} />
      <div className="text-[12px] text-muted">
        {rangesCount === 0
          ? 'Marque os segmentos que entram no Short.'
          : `${rangesCount} ${rangesCount === 1 ? 'trecho contínuo' : 'trechos'} · inclui ${fmtSeconds(
              data.lead_in_s + data.tail_out_s,
            )} de abertura e fim`}
      </div>
      <SegmentedControl label="Tipo do Short" size="sm" options={TIPO_OPTIONS} value={draft.tipo} onChange={(tipo) => onDraft({ ...draft, tipo })} />
      <Field
        label="Título"
        hint={
          finalTitle && finalTitle !== draft.title.trim()
            ? `Publicado como: ${finalTitle}`
            : `Até ${data.limits.title_max} caracteres antes das hashtags${data.hashtag ? `; ${data.hashtag} entra no fim` : ''}.`
        }
      >
        <Input value={draft.title} maxLength={200} onChange={(e) => onDraft({ ...draft, title: e.target.value })} />
      </Field>
      <Field label="Gancho na tela" hint="Fica na faixa do topo da primeira cena e abre a descrição.">
        <Input value={draft.hook} maxLength={200} onChange={(e) => onDraft({ ...draft, hook: e.target.value })} />
      </Field>
      {live.isError ? (
        <div role="alert" className="text-[13px] text-red-300">
          {errorMessage(live.error)}
        </div>
      ) : warnings.length ? (
        <ul className="flex flex-col gap-1" aria-live="polite">
          {warnings.map((w) => (
            <li key={w}>
              <Badge tone="warn" icon={TriangleAlert}>
                {w}
              </Badge>
            </li>
          ))}
        </ul>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button variant="primary" icon={Scissors} busy={creating} disabled={!canCreate} onClick={onCreate}>
          Criar Short
        </Button>
        {rangesCount > 0 || draft.title || draft.hook ? (
          <Button variant="ghost" onClick={onClear}>
            Limpar
          </Button>
        ) : null}
      </div>
    </div>
  );
};
