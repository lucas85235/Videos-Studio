/* Cartão "Rodar pesquisa": acesso à API e à web, cota prevista, Simular, Pesquisar agora, andamento e rotina semanal. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Calculator, Play, Telescope, X } from 'lucide-react';
import React, { useEffect, useState } from 'react';
import { api, type Job } from '../../api';
import { RESEARCH_MARKET_SHORT, RESEARCH_STATUS_LABELS, researchApi, type ResearchPlan, type ResearchState } from '../../apiResearch';
import { isActiveJob } from '../../shell/data';
import { JOBS_QUERY_KEY } from '../../useEvents';
import { Badge, Button, Checkbox, ErrorBox, formatRelative, ProgressBar, Section, useToast } from '../../ui';
import { formatLabel } from './shared';

const PlanText: React.FC<{ plan: ResearchPlan; simulated?: boolean }> = ({ plan, simulated }) => {
  const calls = plan.search_calls ?? plan.searches.length;
  return (
    <p className="text-sm text-muted">
      {simulated ? 'Simulação (com a configuração salva): ' : 'Cota prevista: '}
      <b className="text-fg">
        {calls} {calls === 1 ? 'busca' : 'buscas'}
      </b>{' '}
      do limite de {plan.search_limit ?? 100} por dia{plan.search_left != null ? ` (restam ${plan.search_left} hoje)` : ''} e{' '}
      <b className="text-fg">{plan.total.toLocaleString('pt-BR')}</b> de {plan.budget.toLocaleString('pt-BR')} unidades da cota geral (
      {plan.competitors} {plan.competitors === 1 ? 'concorrente' : 'concorrentes'} = {plan.competitors_units} un.; leituras das buscas ={' '}
      {plan.keyword_units} un.). As buscas e a cota geral do YouTube são divididas com o Radar.
      {plan.pulados.length ? (
        <span className="text-amber-200">
          {' '}
          Ficam de fora ({plan.pulados_motivo === 'buscas do dia' ? 'buscas do dia' : 'orçamento'}): {plan.pulados.join(', ')}.
        </span>
      ) : null}
    </p>
  );
};

const timeText = (iso: string) => new Date(iso).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });

/** Andamento do job com cancelamento (modelo do NextStep). */
const JobProgress: React.FC<{ job: Job }> = ({ job }) => {
  const qc = useQueryClient();
  const live = useQuery({ queryKey: ['job', job.id], queryFn: () => api.get<Job>(`/api/jobs/${job.id}`), initialData: job, refetchInterval: 5_000 });
  const cancel = useMutation({
    mutationFn: () => api.post(`/api/jobs/${job.id}/cancel`),
    onSuccess: () => qc.invalidateQueries({ queryKey: JOBS_QUERY_KEY }),
  });
  const j = live.data ?? job;
  const queued = j.status === 'queued';
  const pct = Math.round(j.progress * 100);
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-line bg-canvas p-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium text-fg">{queued ? 'Pesquisa na fila' : 'Pesquisa em andamento'}</div>
          {j.message ? (
            <div className="truncate text-[13px] text-muted" title={j.message}>
              {j.message}
            </div>
          ) : null}
        </div>
        <span className="text-[13px] tabular-nums text-muted">{queued ? 'Na fila' : `${pct}%`}</span>
        <Button size="sm" variant="secondary" icon={X} busy={cancel.isPending} onClick={() => cancel.mutate()}>
          Cancelar
        </Button>
      </div>
      <ProgressBar
        value={j.progress}
        indeterminate={queued || j.progress <= 0}
        label="Progresso da pesquisa de vídeos"
        valueText={queued ? 'Na fila' : undefined}
      />
      <ErrorBox error={cancel.error} title="Não foi possível cancelar" />
    </div>
  );
};

export const RunCard: React.FC<{
  channelId: string;
  state: ResearchState;
  job: Job | undefined;
  onStarted: (job: Job) => void;
}> = ({ channelId, state, job, onStarted }) => {
  const qc = useQueryClient();
  const { toast } = useToast();
  const [sim, setSim] = useState<ResearchPlan | null>(null);
  // A simulação vale para a config salva de agora: some quando ela (ou a cota prevista) muda, ou o canal troca.
  const planKey = JSON.stringify(state.plan);
  useEffect(() => setSim(null), [channelId, planKey]);
  const running = !!job && isActiveJob(job);
  const canRun = state.access.ok || state.web.ok;
  // 6 h entre rodadas manuais (limite do plano Claude): o botão avisa antes, em vez de o pedido falhar.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!state.next_manual_at) return;
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => window.clearInterval(id);
  }, [state.next_manual_at]);
  const waitUntil = state.next_manual_at && new Date(state.next_manual_at).getTime() > now ? state.next_manual_at : null;

  const simulate = useMutation({ mutationFn: () => researchApi.simulate(channelId), onSuccess: setSim });
  const run = useMutation({
    mutationFn: () => researchApi.run(channelId),
    onSuccess: (j) => {
      onStarted(j);
      qc.invalidateQueries({ queryKey: JOBS_QUERY_KEY });
      toast({
        tone: 'info',
        title: 'Pesquisa de vídeos na fila',
        description: state.web.ok ? 'A síntese com a web pode levar até 30 min. A página atualiza quando terminar.' : 'Sem a web, a rodada guarda só os sinais.',
      });
    },
  });
  const auto = useMutation({
    mutationFn: (on: boolean) => researchApi.saveConfig(channelId, { auto: on }),
    onSuccess: (config) => qc.setQueryData<ResearchState>(['research', 'state', channelId], (old) => (old ? { ...old, config } : old)),
  });
  const last = state.last_run;
  const error = simulate.error || run.error || auto.error;

  return (
    <Section
      title="Rodar pesquisa"
      icon={Telescope}
      description="Junta o desempenho do canal, os concorrentes, as buscas e os comentários; o Claude pesquisa na web e propõe vídeos com data de publicação."
      actions={
        <>
          <Button icon={Calculator} busy={simulate.isPending} onClick={() => simulate.mutate()}>
            Simular
          </Button>
          <Button
            variant="primary"
            icon={Play}
            disabled={!canRun || running || !!waitUntil}
            title={waitUntil ? `Libera às ${timeText(waitUntil)}` : undefined}
            busy={run.isPending}
            onClick={() => run.mutate()}
          >
            {running ? 'Pesquisando…' : 'Pesquisar agora'}
          </Button>
        </>
      }
    >
      <div className="flex flex-wrap items-center gap-2 text-sm">
        {state.access.ok ? (
          <Badge tone="good" title="Concorrentes e buscas usam a YouTube Data API">
            YouTube Data API: {state.access.source}
          </Badge>
        ) : (
          <Badge tone="warn" title={state.access.message}>
            Sem a YouTube Data API
          </Badge>
        )}
        {state.web.ok ? (
          <Badge tone="good" title="A síntese usa o Claude Code com WebSearch e WebFetch (limite do plano)">
            Web: Claude Code
          </Badge>
        ) : (
          <Badge tone="warn" title={state.web.message}>
            Sem busca na web
          </Badge>
        )}
        <Badge title="Formatos que a síntese pode propor (aba Configuração)">{state.formats.map(formatLabel).join(' + ')}</Badge>
        <Badge title="Onde as buscas por palavra-chave rodam">Mercados: {state.config.markets.map((m) => RESEARCH_MARKET_SHORT[m]).join(' + ')}</Badge>
        <Badge>Janela: {state.config.days} dias</Badge>
      </div>
      {!state.access.ok ? <p className="text-[13px] text-amber-200">{state.access.message} Sem ela, concorrentes e buscas ficam de fora (cole uma chave em Radar).</p> : null}
      {!state.web.ok ? (
        <p className="text-[13px] text-amber-200">
          {state.web.message || 'Busca na web indisponível.'} Sem a web, a rodada guarda só os sinais e não cria ideias.
        </p>
      ) : null}
      <PlanText plan={sim ?? state.plan} simulated={!!sim} />
      {last ? (
        <p className="text-xs text-muted">
          Última rodada {last.created_at ? formatRelative(last.created_at) : ''}: {RESEARCH_STATUS_LABELS[last.status] ?? last.status}, {last.n_ideas}{' '}
          {last.n_ideas === 1 ? 'ideia' : 'ideias'} ({last.n_saved} {last.n_saved === 1 ? 'salva' : 'salvas'}), {last.units} unidades de cota
          {last.lacunas.length ? `, ${last.lacunas.length} ${last.lacunas.length === 1 ? 'lacuna' : 'lacunas'}` : ''}.
          {waitUntil && !running ? ` A próxima pesquisa manual libera às ${timeText(waitUntil)} (6 h entre as rodadas, pelo limite do plano Claude).` : ''}
        </p>
      ) : (
        <p className="text-xs text-muted">Nenhuma rodada ainda para este canal.</p>
      )}
      {job && running ? <JobProgress job={job} /> : null}
      <Checkbox
        label="Rodar toda semana"
        description="Na rotina da manhã, um canal por dia, se o canal tiver menos de 15 ideias novas esperando. As ideias aparecem em Hoje."
        checked={state.config.auto}
        disabled={auto.isPending}
        onChange={(v) => auto.mutate(v)}
      />
      <ErrorBox error={error} title="Não foi possível" onClose={() => [simulate, run, auto].forEach((m) => m.reset())} />
    </Section>
  );
};
