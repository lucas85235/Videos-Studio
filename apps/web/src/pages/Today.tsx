import { useQuery } from '@tanstack/react-query';
import { CircleAlert, ListTodo, Package, UserCheck } from 'lucide-react';
import React from 'react';
import { opsApi } from '../apiOps';
import { ErrorBox, Page, PageHeader, Skeleton } from '../ui';
import { StatTile } from './ops/common';
import { PendingSection } from './today/Pending';
import { RoutinesSection } from './today/Routines';
import { TODAY_KEY } from './today/shared';
import { StockSection, YesterdaySection } from './today/StockYesterday';
import { TimeSpentSection } from './today/TimeSpent';
import { WaitingSection } from './today/Waiting';

const SECTION = { waiting: 'esperando', pending: 'pendencias', stock: 'estoque', yesterday: 'ontem' } as const;

const dayTitle = (iso: string) => {
  const d = new Date(`${iso}T12:00:00`);
  const text = d.toLocaleDateString('pt-BR', { weekday: 'long', day: 'numeric', month: 'long' });
  return text.charAt(0).toUpperCase() + text.slice(1);
};

/** Hoje: o que espera você, pendências, estoque, ontem, rotinas e onde vai o tempo. */
export const TodayPage: React.FC = () => {
  const today = useQuery({ queryKey: TODAY_KEY, queryFn: opsApi.today, refetchInterval: 60_000, refetchOnWindowFocus: true });
  const data = today.data;
  const low = data?.stock.filter((s) => s.below).length ?? 0;
  return (
    <Page>
      <PageHeader title="Hoje" description={data ? dayTitle(data.date) : 'O que fazer hoje: o que espera você, pendências e estoque.'} />
      {today.isLoading ? (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4" aria-hidden="true">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-24" />
          ))}
        </div>
      ) : null}
      <ErrorBox error={today.error} title="Não foi possível carregar o dia" onRetry={() => today.refetch()} />
      {data ? (
        <div className="flex flex-col gap-6">
          <nav aria-label="Resumo do dia" className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <StatTile target={SECTION.waiting} icon={UserCheck} label="Esperando você" value={data.waiting.length} tone={data.waiting.length ? 'warn' : 'good'} />
            <StatTile target={SECTION.pending} icon={ListTodo} label="Pendências" value={data.pending.length} />
            <StatTile
              target={SECTION.stock}
              icon={Package}
              label="Estoque"
              value={data.stock.length ? `${data.stock.length - low}/${data.stock.length}` : '—'}
              tone={low ? 'warn' : 'neutral'}
              hint={data.stock.length ? (low ? `${low} abaixo do mínimo` : 'Canais em dia') : 'Sem mínimo definido'}
            />
            <StatTile
              target={SECTION.yesterday}
              icon={CircleAlert}
              label="Falhas ontem"
              value={data.yesterday.failed.length}
              tone={data.yesterday.failed.length ? 'warn' : 'neutral'}
              hint={`${data.yesterday.published.length} ${data.yesterday.published.length === 1 ? 'publicado' : 'publicados'}`}
            />
          </nav>
          <div id={SECTION.waiting}>
            <WaitingSection items={data.waiting} />
          </div>
          <div id={SECTION.pending}>
            <PendingSection items={data.pending} />
          </div>
          <div className="grid gap-6 lg:grid-cols-2">
            <div id={SECTION.stock}>
              <StockSection items={data.stock} />
            </div>
            <div id={SECTION.yesterday}>
              <YesterdaySection data={data.yesterday} />
            </div>
          </div>
          <RoutinesSection state={data.routines} />
          <TimeSpentSection />
        </div>
      ) : null}
    </Page>
  );
};
