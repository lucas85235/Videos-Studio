import { useQuery } from '@tanstack/react-query';
import { Gauge, Lightbulb, TrendingDown, TrendingUp } from 'lucide-react';
import React, { useState } from 'react';
import { opsApi } from '../../apiOps';
import { ErrorBox, formatMoney, SegmentedControl, Section, Skeleton } from '../../ui';
import { comma, dur, pct } from './shared';

const PERIODS = [
  { id: '7', label: '7 dias' },
  { id: '14', label: '14 dias' },
  { id: '30', label: '30 dias' },
] as const;
type Period = (typeof PERIODS)[number]['id'];

/** Onde vai o tempo: tempo por etapa (a partir das tarefas do app), falhas, retrabalho e sugestões. */
export const TimeSpentSection: React.FC = () => {
  const [days, setDays] = useState<Period>('7');
  const vel = useQuery({ queryKey: ['ops', 'velocity', days], queryFn: () => opsApi.velocity(Number(days)) });
  const data = vel.data;
  const delta = data && data.total_prev_s ? (data.total_s - data.total_prev_s) / data.total_prev_s : null;
  return (
    <Section
      title="Onde vai o tempo"
      icon={Gauge}
      description="Tempo das tarefas do app por etapa (revisões fora do app não entram). Retrabalho = 1,5 ou mais execuções por projeto em 3 ou mais projetos."
      actions={<SegmentedControl label="Período" size="sm" options={[...PERIODS]} value={days} onChange={setDays} />}
    >
      {vel.isLoading ? <Skeleton className="h-32" /> : null}
      <ErrorBox error={vel.error} title="Não foi possível calcular o tempo" onRetry={() => vel.refetch()} />
      {data ? (
        <>
          <p className="flex flex-wrap items-center gap-x-2 text-sm text-muted">
            <span>
              <span className="font-semibold text-fg">{dur(data.total_s)}</span> em {data.jobs} {data.jobs === 1 ? 'tarefa' : 'tarefas'}
              {data.failures ? `, ${data.failures} ${data.failures === 1 ? 'falhou' : 'falharam'}` : ''}
              {data.cost_usd ? `, ${formatMoney(data.cost_usd)}` : ''}.
            </span>
            {delta !== null ? (
              <span className="inline-flex items-center gap-1">
                {delta > 0 ? <TrendingUp size={14} aria-hidden="true" /> : <TrendingDown size={14} aria-hidden="true" />}
                {delta > 0 ? '+' : ''}
                {pct(delta)} contra os {days} dias anteriores
              </span>
            ) : null}
          </p>
          {data.suggestions.length ? (
            <ul className="flex flex-col gap-1.5">
              {data.suggestions.map((s) => (
                <li key={s} className="flex items-start gap-2 rounded-lg border border-amber-300/35 bg-amber-300/10 px-3 py-2 text-sm text-fg">
                  <Lightbulb size={16} aria-hidden="true" className="mt-0.5 shrink-0 text-amber-200" />
                  {s}
                </li>
              ))}
            </ul>
          ) : null}
          {data.steps.length === 0 ? (
            <p className="text-sm text-muted">Nenhuma tarefa terminou neste período.</p>
          ) : (
            <div className="overflow-x-auto rounded-lg border border-line">
              <table className="w-full min-w-[640px] text-sm">
                <caption className="sr-only">Tempo por etapa nos últimos {days} dias</caption>
                <thead className="bg-raised text-left text-[13px] text-muted">
                  <tr>
                    <th scope="col" className="px-3 py-2 font-medium">Etapa</th>
                    <th scope="col" className="px-3 py-2 text-right font-medium">Vezes</th>
                    <th scope="col" className="px-3 py-2 text-right font-medium">Total</th>
                    <th scope="col" className="px-3 py-2 text-right font-medium">Parte</th>
                    <th scope="col" className="px-3 py-2 text-right font-medium">Média</th>
                    <th scope="col" className="px-3 py-2 text-right font-medium">Por projeto</th>
                    <th scope="col" className="px-3 py-2 text-right font-medium">Falhas</th>
                    <th scope="col" className="px-3 py-2 text-right font-medium">Antes</th>
                    <th scope="col" className="px-3 py-2 text-right font-medium">Custo</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line tabular-nums">
                  {data.steps.map((s) => (
                    <tr key={s.kind}>
                      <th scope="row" className="px-3 py-2 text-left font-medium text-fg">{s.label}</th>
                      <td className="px-3 py-2 text-right">{s.n}</td>
                      <td className="px-3 py-2 text-right">{dur(s.total_s)}</td>
                      <td className="px-3 py-2 text-right">{pct(s.pct)}</td>
                      <td className="px-3 py-2 text-right">{dur(s.avg_s)}</td>
                      <td className="px-3 py-2 text-right">
                        {s.per_project != null ? `${comma(s.per_project)}× (${s.projects})` : '—'}
                      </td>
                      <td className={`px-3 py-2 text-right ${s.failures ? 'text-red-300' : ''}`}>{s.failures}</td>
                      <td className="px-3 py-2 text-right text-subtle">{s.before_s ? dur(s.before_s) : '—'}</td>
                      <td className="px-3 py-2 text-right">{s.cost_usd ? formatMoney(s.cost_usd) : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      ) : null}
    </Section>
  );
};
