import { useQuery, useQueryClient } from '@tanstack/react-query';
import { BookOpen, Globe, LayoutDashboard, Radar, TrendingUp, Users } from 'lucide-react';
import React, { useEffect, useRef, useState } from 'react';
import { api, type Channel } from '../api';
import { radarApi } from '../apiRadar';
import { isActiveJob, useJobs } from '../shell/data';
import { Empty, ErrorBox, Page, PageHeader, Select, Skeleton, tabPanelProps, Tabs, useToast } from '../ui';
import { AccessCard } from './radar/AccessCard';
import { ArbitrageTab, MethodTab } from './radar/ArbitrageTab';
import { ChannelDrawer, type ChannelFilters, ChannelsTab, DEFAULT_CHANNEL_FILTERS } from './radar/ChannelsTab';
import { OutliersTab } from './radar/OutliersTab';
import { Overview } from './radar/Overview';

const TABS = [
  { id: 'overview', label: 'Visão geral', icon: LayoutDashboard },
  { id: 'channels', label: 'Canais', icon: Users },
  { id: 'outliers', label: 'Vídeos fora da curva', icon: TrendingUp },
  { id: 'arb', label: 'Arbitragem EUA → Brasil', icon: Globe },
  { id: 'method', label: 'Método', icon: BookOpen },
];
const ID = 'radar';

/** Radar de nichos e vídeos fora da curva (ADR 0003, bloco E; origem: radar de nichos do canais). */
export const RadarPage: React.FC = () => {
  const qc = useQueryClient();
  const { toast } = useToast();
  const [tab, setTab] = useState('overview');
  const [runId, setRunId] = useState<number | null>(null);
  const [filters, setFilters] = useState<ChannelFilters>(DEFAULT_CHANNEL_FILTERS);
  const [openChannel, setOpenChannel] = useState<string | null>(null);

  const state = useQuery({ queryKey: ['radar', 'state'], queryFn: radarApi.state });
  const runs = useQuery({ queryKey: ['radar', 'runs'], queryFn: radarApi.runs });
  const channels = useQuery({ queryKey: ['channels'], queryFn: () => api.get<Channel[]>('/api/channels') });
  const okRuns = (runs.data ?? []).filter((r) => r.status === 'ok');
  const chosen = runId ?? okRuns[0]?.id ?? null;
  const panel = useQuery({ queryKey: ['radar', 'panel', chosen], queryFn: () => radarApi.panel(chosen!), enabled: chosen != null });

  // Coleta terminou: recarrega o radar e avisa.
  const jobs = useJobs();
  const radarJob = (jobs.data ?? []).find((j) => j.kind === 'radar_collect');
  const wasActive = useRef(false);
  useEffect(() => {
    const active = radarJob ? isActiveJob(radarJob) : false;
    if (wasActive.current && !active && radarJob) {
      setRunId(null);
      qc.invalidateQueries({ queryKey: ['radar'] });
      if (radarJob.status === 'failed') toast({ tone: 'bad', title: 'A coleta do radar falhou', description: radarJob.error ?? undefined });
      else if (radarJob.status === 'done') toast({ tone: 'good', title: 'Radar atualizado' });
    }
    wasActive.current = active;
  }, [radarJob, qc, toast]);

  const goNiche = (name: string) => {
    setFilters({ ...DEFAULT_CHANNEL_FILTERS, niche: name });
    setTab('channels');
  };
  const data = panel.data;

  return (
    <Page width="wide">
      <PageHeader
        title="Radar"
        description="Nichos com demanda, canais novos que crescem e vídeos fora da curva no YouTube (EUA em inglês e Brasil em português)."
        meta={data ? <span>{data.meta.description}</span> : null}
        actions={
          okRuns.length > 1 ? (
            <Select aria-label="Coleta" value={chosen ?? ''} onChange={(e) => setRunId(Number(e.target.value))}>
              {okRuns.map((r) => (
                <option key={r.id} value={r.id}>
                  Coleta #{r.id} · {r.created_at ? new Date(r.created_at).toLocaleDateString('pt-BR') : ''}
                </option>
              ))}
            </Select>
          ) : null
        }
      />
      <div className="flex flex-col gap-4">
        <ErrorBox error={state.error || runs.error} onRetry={() => (state.refetch(), runs.refetch())} />
        {state.data ? <AccessCard state={state.data} panelRunId={chosen} /> : <Skeleton className="h-40" />}
        {runs.isLoading || panel.isLoading ? (
          <Skeleton className="h-96" />
        ) : panel.error ? (
          <ErrorBox error={panel.error} onRetry={() => panel.refetch()} />
        ) : !data ? (
          <Empty icon={Radar} title="Nenhuma coleta ainda">
            Salve uma chave da YouTube Data API (ou vincule um canal em Canais), use "Simular" para ver a cota e depois "Coletar agora".
          </Empty>
        ) : (
          <>
            <Tabs tabs={TABS} active={tab} onChange={setTab} ariaLabel="Partes do radar" idPrefix={ID} />
            <div {...tabPanelProps(tab, ID)}>
              {tab === 'overview' ? (
                <Overview data={data} channels={channels.data ?? []} onNiche={goNiche} onChannel={setOpenChannel} />
              ) : tab === 'channels' ? (
                <ChannelsTab data={data} filters={filters} setFilters={setFilters} onOpen={setOpenChannel} />
              ) : tab === 'outliers' ? (
                <OutliersTab data={data} channels={channels.data ?? []} onOpen={setOpenChannel} />
              ) : tab === 'arb' ? (
                <ArbitrageTab data={data} onNiche={goNiche} />
              ) : (
                <MethodTab data={data} />
              )}
            </div>
            {openChannel ? <ChannelDrawer data={data} id={openChannel} channels={channels.data ?? []} onClose={() => setOpenChannel(null)} /> : null}
          </>
        )}
      </div>
    </Page>
  );
};
