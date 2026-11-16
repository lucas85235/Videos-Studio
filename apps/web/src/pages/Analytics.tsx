import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { BarChart3, BookOpen, FlaskConical, Pencil, Plus, RefreshCw, Sparkles, Table2, TriangleAlert } from 'lucide-react';
import React, { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, type Channel } from '../api';
import { analyticsApi, type CollectState } from '../apiAnalytics';
import { isActiveJob, useJobs } from '../shell/data';
import { Button, ErrorBox, formatRelative, Page, PageHeader, Section, Select, Skeleton, useToast } from '../ui';
import { NewExperiment, OpenExperiments } from './analytics/Experiments';
import { AppliedLessons, ProposedLessons } from './analytics/Lessons';
import { ManualMetrics } from './analytics/ManualMetrics';
import { VideosTable } from './analytics/VideosTable';
import { Suggestions, WhatWorked } from './analytics/WhatWorked';

const ALL = '';

const STATE_TEXT: Record<CollectState['state'], string> = {
  ok: 'coleta em dia',
  sem_escopo: 'sem retenção (falta permissão do Analytics)',
  sem_token: 'sem acesso ao YouTube',
  publico: 'ligado só pelo link público',
  erro: 'a última coleta falhou',
  nunca: 'ainda não coletado',
};

const REACH_TEXT: Record<string, string> = {
  ok: 'em dia',
  esperando: 'esperando o primeiro relatório do YouTube (até 2 dias)',
  sem_escopo: 'falta permissão do Analytics (vincule de novo em Canais)',
  erro: 'a última leitura falhou',
};

const REVENUE_TEXT: Record<string, string> = {
  ok: 'ligada (entra em Dinheiro, loja youtube)',
  desligada: 'desligada; para ligar, vincule o canal de novo em Canais marcando “Trazer a receita do YouTube”',
  sem_escopo: 'sem permissão; vincule de novo em Canais marcando a receita e deixe a receita marcada na tela do Google',
  erro: 'a última leitura falhou',
};

/** Impressões/CTR e receita do YouTube por canal (só canais que já passaram por uma coleta com acesso). */
const ExtraStates: React.FC<{ states: CollectState[] }> = ({ states }) => {
  const shown = states.filter((s) => s.reach || s.revenue);
  if (!shown.length) return null;
  return (
    <div className="mb-4 flex flex-col gap-1 rounded-lg border border-line bg-card p-3 text-[13px] text-muted">
      {shown.map((s) => (
        <p key={s.channel_id}>
          <span className="font-medium text-fg">{s.name}</span>
          {s.reach ? (
            <>
              {' · '}Impressões e CTR: {REACH_TEXT[s.reach] ?? s.reach}
              {s.reach === 'erro' && s.reach_error ? ` (${s.reach_error.slice(0, 160)})` : ''}
            </>
          ) : null}
          {s.revenue ? (
            <>
              {' · '}Receita do YouTube: {REVENUE_TEXT[s.revenue] ?? s.revenue}
              {s.revenue_error ? ` (${s.revenue_error.slice(0, 160)})` : ''}
            </>
          ) : null}
          .
        </p>
      ))}
      <p className="text-subtle">
        Impressões, CTR e receita lançados à mão continuam valendo quando a coleta não traz o número. Impressões e CTR coletados contam a partir de 30
        dias antes de o relatório ser ligado: em vídeo mais antigo que isso, não são o total da vida do vídeo.
      </p>
    </div>
  );
};

/** Avisos da coleta: escopo do Analytics faltando, canal sem acesso ou erro. */
const CollectNotices: React.FC<{ states: CollectState[] }> = ({ states }) => {
  const missing = states.filter((s) => s.state === 'sem_escopo');
  const others = states.filter((s) => s.state === 'sem_token' || s.state === 'publico' || s.state === 'erro');
  if (!missing.length && !others.length) return null;
  return (
    <div role="status" className="mb-4 flex gap-3 rounded-lg border border-amber-300/35 bg-amber-300/10 p-3 text-sm">
      <TriangleAlert size={18} aria-hidden="true" className="mt-0.5 shrink-0 text-amber-200" />
      <div className="flex min-w-0 flex-col gap-1">
        {missing.length ? (
          <p className="text-fg">
            <Link to="/canais" className="font-medium underline">
              Vincule o canal de novo em Canais
            </Link>{' '}
            para trazer retenção e duração média ({missing.map((s) => s.name).join(', ')}). Views, likes e comentários já vêm.
          </p>
        ) : null}
        {others.map((s) => (
          <p key={s.channel_id} className="text-muted">
            {s.name}: {STATE_TEXT[s.state]}
            {s.state === 'erro' && s.error ? ` (${s.error.slice(0, 160)})` : ''}.
          </p>
        ))}
      </div>
    </div>
  );
};

/** Desempenho: números dos vídeos, o que funcionou, experimentos e lições. */
export const AnalyticsPage: React.FC = () => {
  const client = useQueryClient();
  const { toast } = useToast();
  const [channelId, setChannelId] = useState<string>(ALL);
  const [creating, setCreating] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const channel = channelId || null;

  const channels = useQuery({ queryKey: ['channels'], queryFn: () => api.get<Channel[]>('/api/channels') });
  const overview = useQuery({ queryKey: ['analytics-overview', channel], queryFn: () => analyticsApi.overview(channel) });
  const variables = useQuery({ queryKey: ['analytics-variables'], queryFn: analyticsApi.variables, staleTime: Infinity });
  const jobs = useJobs();

  const refresh = () => {
    client.invalidateQueries({ queryKey: ['analytics-overview'] });
    client.invalidateQueries({ queryKey: ['analytics-lessons'] });
  };

  const collect = useMutation({
    mutationFn: () => analyticsApi.collect(channel),
    onSuccess: (job) => {
      setJobId(job.id);
      toast({ tone: 'info', title: 'Atualizando métricas', description: 'A coleta roda em segundo plano.' });
    },
  });

  const running = (jobs.data ?? []).some((j) => j.kind === 'metrics_collect' && isActiveJob(j));
  const tracked = jobId ? (jobs.data ?? []).find((j) => j.id === jobId) : undefined;
  useEffect(() => {
    if (!tracked || isActiveJob(tracked)) return;
    setJobId(null);
    client.invalidateQueries({ queryKey: ['analytics-overview'] });
    if (tracked.status === 'failed') toast({ tone: 'bad', title: 'A coleta falhou', description: tracked.error ?? undefined });
    else if (tracked.status === 'done') toast({ tone: 'good', title: 'Métricas atualizadas' });
  }, [tracked, client, toast]);

  const channelNames = useMemo(() => Object.fromEntries((channels.data ?? []).map((c) => [c.id, c.name])), [channels.data]);
  const labels = useMemo(() => Object.fromEntries((variables.data ?? []).map((v) => [v.id, v.label])), [variables.data]);
  const data = overview.data;
  const lastRun = (data?.collect ?? [])
    .map((s) => s.last_run)
    .filter((x): x is string => Boolean(x))
    .sort()
    .pop();

  return (
    <Page>
      <PageHeader
        title="Desempenho"
        description="Como os vídeos foram: números, o que funcionou, experimentos e lições."
        meta={lastRun ? <span>Última coleta {formatRelative(lastRun)}</span> : <span>Nenhuma coleta ainda</span>}
        actions={
          <>
            <Select aria-label="Canal" value={channelId} onChange={(e) => setChannelId(e.target.value)} className="w-auto min-w-44">
              <option value={ALL}>Todos os canais</option>
              {(channels.data ?? []).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </Select>
            <Button variant="primary" icon={RefreshCw} onClick={() => collect.mutate()} busy={collect.isPending || running}>
              {running ? 'Atualizando…' : 'Atualizar métricas'}
            </Button>
          </>
        }
      />
      <ErrorBox error={collect.error} title="Não deu para atualizar" className="mb-4" onClose={() => collect.reset()} />
      {data ? <CollectNotices states={data.collect} /> : null}
      {data ? <ExtraStates states={data.collect} /> : null}
      {overview.isLoading ? (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-40" />
          <Skeleton className="h-64" />
        </div>
      ) : null}
      <ErrorBox error={overview.error} onRetry={() => overview.refetch()} />
      {data ? (
        <div className="flex flex-col gap-6">
          {data.suggestions.length ? (
            <Section title="Sugestões" icon={Sparkles} description="Tiradas dos Shorts que já passaram da idade mínima.">
              <Suggestions items={data.suggestions} />
            </Section>
          ) : null}
          <Section title="O que funcionou" icon={BarChart3} description="Mediana de views dos Shorts por variável (n = número de vídeos).">
            <WhatWorked results={data.results} />
          </Section>
          <Section
            title="Experimentos abertos"
            icon={FlaskConical}
            description="Vencedor só quando todas as variantes têm 3+ vídeos e a primeira tem 1,3x a mediana da segunda."
            actions={
              !creating ? (
                <Button size="sm" icon={Plus} onClick={() => setCreating(true)}>
                  Novo experimento
                </Button>
              ) : null
            }
          >
            {creating ? (
              <NewExperiment
                channelId={channel}
                onCancel={() => setCreating(false)}
                onDone={() => {
                  setCreating(false);
                  refresh();
                }}
              />
            ) : null}
            <OpenExperiments experiments={data.experiments} labels={labels} onChanged={refresh} />
          </Section>
          <Section title="Lições propostas" icon={BookOpen} description="O que aprendeu e ainda falta aplicar (ou descartar).">
            <ProposedLessons channelId={channel} channels={channelNames} />
          </Section>
          <div id="licoes-aplicadas">
            <Section
              title="Lições aplicadas"
              icon={BookOpen}
              description="Entram sozinhas no pedido do roteiro e dos metadados dos próximos vídeos. Retire a que não ajudou."
            >
              <AppliedLessons channelId={channel} channels={channelNames} />
            </Section>
          </div>
          <Section title="Vídeos" icon={Table2} description="Última leitura de cada vídeo. Retenção vazia = o YouTube Analytics ainda não mandou.">
            <VideosTable videos={data.videos} />
          </Section>
          <Section title="Lançar métricas à mão" icon={Pencil} description="Para números que a coleta não traz (ou vídeos fora do Vulto).">
            <ManualMetrics channelId={channel} onSaved={refresh} />
          </Section>
        </div>
      ) : null}
    </Page>
  );
};
