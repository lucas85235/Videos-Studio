import { useQuery } from '@tanstack/react-query';
import { Tv, Wallet } from 'lucide-react';
import React, { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, type Channel } from '../api';
import { businessApi } from '../apiBusiness';
import { useProjectsIndex } from '../shell/data';
import { buttonClass, Empty, ErrorBox, Field, Page, PageHeader, Select, Skeleton } from '../ui';
import { moneyKeys } from './money/common';
import { Fronts } from './money/Fronts';
import { LinksConfig } from './money/LinksConfig';
import { Offers } from './money/Offers';
import { Revenue } from './money/Revenue';

const CHANNEL_KEY = 'vulto.dinheiro.canal';
const readChannel = () => {
  try {
    return window.localStorage.getItem(CHANNEL_KEY) ?? '';
  } catch {
    return '';
  }
};

const ChannelMoney: React.FC<{ channelId: string }> = ({ channelId }) => {
  const config = useQuery({ queryKey: moneyKeys.config(channelId), queryFn: () => businessApi.monetization(channelId) });
  const summary = useQuery({ queryKey: moneyKeys.offers(channelId), queryFn: () => businessApi.summary({ channel_id: channelId }) });
  const revenue = useQuery({ queryKey: moneyKeys.revenue(channelId), queryFn: () => businessApi.revenue({ channel_id: channelId, months: 12 }) });
  const projects = useProjectsIndex();
  const titles = useMemo(() => new Map((projects.data ?? []).map((p) => [p.id, p.title])), [projects.data]);
  const data = summary.data?.channels[0];

  if (config.isLoading || summary.isLoading) {
    return (
      <div className="flex flex-col gap-4">
        <Skeleton className="h-32" />
        <Skeleton className="h-48" />
      </div>
    );
  }
  if (config.error || summary.error || !config.data) {
    return <ErrorBox error={config.error || summary.error} onRetry={() => (config.refetch(), summary.refetch())} />;
  }
  return (
    <div className="flex flex-col gap-4">
      <Fronts channelId={channelId} config={config.data} />
      <Offers channelId={channelId} offers={data?.offers ?? []} />
      <Revenue channelId={channelId} months={revenue.data?.summary ?? []} entries={revenue.data?.entries ?? []} />
      <ErrorBox error={revenue.error} />
      <LinksConfig channelId={channelId} config={config.data} projectTitles={titles} />
    </div>
  );
};

/** Dinheiro: frentes, ofertas de afiliado, links na descrição e receita de cada canal. */
export const MoneyPage: React.FC = () => {
  const channels = useQuery({ queryKey: ['channels'], queryFn: () => api.get<Channel[]>('/api/channels') });
  const [channel, setChannel] = useState(readChannel);
  const list = channels.data ?? [];
  const current = list.find((c) => c.id === channel)?.id ?? list[0]?.id ?? '';
  useEffect(() => {
    try {
      if (current) window.localStorage.setItem(CHANNEL_KEY, current);
    } catch {
      /* sem armazenamento: só não lembra o canal */
    }
  }, [current]);

  return (
    <Page>
      <PageHeader
        title="Dinheiro"
        description="Como cada canal ganha dinheiro além do AdSense: frentes, ofertas de afiliado, links na descrição e receita por mês."
        actions={
          list.length > 1 ? (
            <Field label="Canal" className="w-56">
              <Select value={current} onChange={(e) => setChannel(e.target.value)}>
                {list.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </Select>
            </Field>
          ) : null
        }
      />
      <ErrorBox error={channels.error} onRetry={() => channels.refetch()} />
      {channels.isLoading ? (
        <Skeleton className="h-40" />
      ) : current ? (
        <ChannelMoney key={current} channelId={current} />
      ) : (
        <Empty
          icon={Wallet}
          title="Nenhum canal ainda"
          action={
            <Link to="/canais" className={buttonClass({ variant: 'primary' })}>
              <Tv size={16} aria-hidden="true" />
              Criar canal
            </Link>
          }
        >
          A monetização é por canal. Crie um canal para cadastrar ofertas e lançar a receita.
        </Empty>
      )}
    </Page>
  );
};
