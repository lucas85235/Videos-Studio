import { useQuery, useQueryClient } from '@tanstack/react-query';
import { History, Lightbulb, MessageCircleQuestion, Settings2, Telescope, TrendingUp, Tv } from 'lucide-react';
import React, { useCallback, useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api, type Channel, type Job } from '../api';
import { researchApi, type ResearchRunSummary } from '../apiResearch';
import { Badge, Button, buttonClass, Empty, ErrorBox, Page, PageHeader, Select, Skeleton, tabPanelProps, Tabs, useToast } from '../ui';
import { useJobDone } from './project/proof/useJobDone';
import { ConfigTab } from './research/ConfigTab';
import { HistoryTab } from './research/HistoryTab';
import { RunCard } from './research/RunCard';
import { runDateText } from './research/shared';
import { AudienceTab, OutliersTab, OwnTab } from './research/SignalsTabs';
import { SuggestionsTab } from './research/SuggestionsTab';

const TAB_IDS = ['sugestoes', 'fora', 'canal', 'publico', 'config', 'historico'] as const;
type TabId = (typeof TAB_IDS)[number];
const ID = 'pesquisa';
const RUN_TABS: TabId[] = ['sugestoes', 'fora', 'canal', 'publico'];

const count = (n: number | undefined) => (n ? <Badge className="ml-1">{n}</Badge> : undefined);

/** Pesquisa de vídeos do canal: sinais do canal, concorrentes, buscas e comentários + síntese com a web → ideias com data. */
export const VideoResearchPage: React.FC = () => {
  const qc = useQueryClient();
  const { toast } = useToast();
  const [params, setParams] = useSearchParams();
  const channelId = params.get('canal') ?? '';
  const rawTab = params.get('aba') as TabId | null;
  const tab: TabId = rawTab && TAB_IDS.includes(rawTab) ? rawTab : 'sugestoes';
  const [openRun, setOpenRun] = useState<string | null>(null);
  const [startedJob, setStartedJob] = useState<Job | null>(null);

  const channels = useQuery({ queryKey: ['channels'], queryFn: () => api.get<Channel[]>('/api/channels') });
  const list = channels.data ?? [];
  const channel = list.find((c) => c.id === channelId);

  const setParam = useCallback(
    (key: string, value: string | null) =>
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (value) next.set(key, value);
          else next.delete(key);
          return next;
        },
        { replace: true },
      ),
    [setParams],
  );

  // Sem canal na URL (ou canal que não existe mais): abre o primeiro.
  useEffect(() => {
    if (channels.data?.length && !channels.data.some((c) => c.id === channelId)) setParam('canal', channels.data[0].id);
  }, [channels.data, channelId, setParam]);
  useEffect(() => {
    setOpenRun(null);
    setStartedJob(null);
  }, [channelId]);

  const state = useQuery({
    queryKey: ['research', 'state', channelId],
    queryFn: () => researchApi.state(channelId),
    enabled: !!channel,
  });
  const lastRunId = state.data?.last_run?.id ?? null;
  const runId = openRun ?? lastRunId;
  const run = useQuery({ queryKey: ['research', 'run', runId], queryFn: () => researchApi.getRun(runId!), enabled: !!runId });

  // O job da pesquisa: o que esta tela disparou ou o que já estava rodando (outra aba, rotina semanal).
  const jobId = startedJob?.id ?? state.data?.active_job?.id ?? null;
  const job: Job | undefined = useJobDone(jobId, (j) => {
    setStartedJob(null);
    setOpenRun(null);
    qc.invalidateQueries({ queryKey: ['research'] });
    qc.invalidateQueries({ queryKey: ['ideas'] });
    // O aviso de fim (pronta, parcial ou falhou) vem do aviso geral dos jobs (useEvents); aqui só o cancelamento.
    if (j.status === 'done') setParam('aba', null);
    else if (j.status === 'canceled') toast({ tone: 'info', title: 'Pesquisa cancelada' });
  });
  const activeJob = state.data?.active_job ?? undefined;
  const shownJob = job ?? (startedJob ?? (activeJob && activeJob.id === jobId ? activeJob : undefined));

  const data = run.data;
  const sig = data?.signals;
  const tabs = [
    { id: 'sugestoes', label: 'Sugestões', icon: Lightbulb, badge: count(data?.n_ideas) },
    { id: 'fora', label: 'Fora da curva', icon: TrendingUp, badge: count((sig?.competitors?.items.length ?? 0) + (sig?.keywords?.items.length ?? 0)) },
    { id: 'canal', label: 'Meu canal', icon: Tv },
    { id: 'publico', label: 'Público', icon: MessageCircleQuestion, badge: count(sig?.comments?.items.length) },
    { id: 'config', label: 'Configuração', icon: Settings2 },
    { id: 'historico', label: 'Histórico', icon: History },
  ];
  const openOld = (r: ResearchRunSummary) => {
    setOpenRun(r.id === lastRunId ? null : r.id);
    setParam('aba', null);
  };

  if (channels.isLoading) {
    return (
      <Page width="wide">
        <PageHeader title="Pesquisa de vídeos" />
        <Skeleton className="h-96" />
      </Page>
    );
  }
  if (channels.error && !list.length) {
    // Sem a lista de canais não dá para saber se há canais: só o erro (nada de "crie um canal").
    return (
      <Page width="wide">
        <PageHeader title="Pesquisa de vídeos" />
        <ErrorBox error={channels.error} title="Não deu para carregar os canais" onRetry={() => channels.refetch()} />
      </Page>
    );
  }
  if (!list.length) {
    return (
      <Page width="wide">
        <PageHeader title="Pesquisa de vídeos" />
        <Empty
          icon={Telescope}
          title="Nenhum canal ainda"
          action={
            <Link to="/canais" className={buttonClass({ variant: 'primary' })}>
              Ir para Canais
            </Link>
          }
        >
          A pesquisa é por canal: crie um canal (e vincule o YouTube para ler o desempenho dele).
        </Empty>
      </Page>
    );
  }

  return (
    <Page width="wide">
      <PageHeader
        title="Pesquisa de vídeos"
        description="O que publicar e quando: o desempenho do canal, os concorrentes, as buscas no Brasil e nos EUA e os pedidos do público viram ideias com data, nota e prova."
        meta={
          openRun && data ? (
            <span className="inline-flex flex-wrap items-center gap-2">
              Vendo a rodada de {runDateText(data.created_at)}
              <Button size="sm" variant="ghost" onClick={() => setOpenRun(null)}>
                Voltar à última
              </Button>
            </span>
          ) : null
        }
        actions={
          <Select aria-label="Canal" value={channelId} onChange={(e) => setParam('canal', e.target.value)}>
            {list.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </Select>
        }
      />
      <div className="flex flex-col gap-4">
        <ErrorBox error={state.error || channels.error} onRetry={() => (state.refetch(), channels.refetch())} />
        {state.data ? (
          <RunCard key={channelId} channelId={channelId} state={state.data} job={shownJob} onStarted={setStartedJob} />
        ) : state.error ? null : (
          <Skeleton className="h-40" />
        )}
        <Tabs tabs={tabs} active={tab} onChange={(id) => setParam('aba', id === 'sugestoes' ? null : id)} ariaLabel="Partes da pesquisa" idPrefix={ID} />
        <div {...tabPanelProps(tab, ID)}>
          {tab === 'config' ? (
            state.data ? (
              <ConfigTab key={channelId} channelId={channelId} state={state.data} />
            ) : state.error ? null : (
              <Skeleton className="h-64" />
            )
          ) : tab === 'historico' ? (
            <HistoryTab channelId={channelId} openRunId={runId} onOpen={openOld} />
          ) : RUN_TABS.includes(tab) && !runId ? (
            state.isLoading ? (
              <Skeleton className="h-64" />
            ) : (
              <Empty icon={Telescope} title={`Nenhuma pesquisa do ${channel?.name ?? 'canal'} ainda`}>
                Confira a aba Configuração (concorrentes, palavras-chave e mercados), use "Simular" para ver a cota e depois "Pesquisar agora".
              </Empty>
            )
          ) : run.isLoading ? (
            <Skeleton className="h-96" />
          ) : run.error || !data ? (
            <ErrorBox error={run.error} onRetry={() => run.refetch()} />
          ) : tab === 'sugestoes' ? (
            <SuggestionsTab key={data.id} run={data} />
          ) : tab === 'fora' ? (
            <OutliersTab key={data.id} run={data} />
          ) : tab === 'canal' ? (
            <OwnTab run={data} formats={data.params.formats ?? state.data?.formats ?? []} />
          ) : (
            <AudienceTab key={data.id} run={data} />
          )}
        </div>
      </div>
    </Page>
  );
};
