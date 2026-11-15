/* Cartão "Acesso e coleta" do radar: chave da API, simular, coletar, rodar toda semana, refazer contas e catálogo. */
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Calculator, KeyRound, ListChecks, Play, RefreshCw, Trash2 } from 'lucide-react';
import React, { useState } from 'react';
import { radarApi, type RadarSimulation, type RadarState } from '../../apiRadar';
import { isActiveJob, useJobs } from '../../shell/data';
import { JOBS_QUERY_KEY } from '../../useEvents';
import { Badge, Button, Checkbox, ErrorBox, Field, formatRelative, Input, Section, useToast } from '../../ui';
import { CatalogEditor } from './CatalogEditor';

export const AccessCard: React.FC<{ state: RadarState; panelRunId: number | null }> = ({ state, panelRunId }) => {
  const qc = useQueryClient();
  const { toast } = useToast();
  const jobs = useJobs();
  const [key, setKey] = useState('');
  const [sim, setSim] = useState<RadarSimulation | null>(null);
  const [editing, setEditing] = useState(false);
  const refresh = () => qc.invalidateQueries({ queryKey: ['radar'] });
  const running = (jobs.data ?? []).some((j) => j.kind === 'radar_collect' && isActiveJob(j));

  const saveKey = useMutation({
    mutationFn: () => radarApi.setKey(key.trim()),
    onSuccess: () => {
      setKey('');
      refresh();
      toast({ tone: 'good', title: 'Chave salva' });
    },
  });
  const dropKey = useMutation({ mutationFn: radarApi.deleteKey, onSuccess: refresh });
  const simulate = useMutation({ mutationFn: () => radarApi.simulate(), onSuccess: setSim });
  const collect = useMutation({
    mutationFn: () => radarApi.collect(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: JOBS_QUERY_KEY });
      refresh();
      toast({ tone: 'info', title: 'Coleta do radar na fila', description: 'Leva alguns minutos. A página atualiza quando terminar.' });
    },
  });
  const auto = useMutation({ mutationFn: (on: boolean) => radarApi.settings(on), onSuccess: (s) => qc.setQueryData(['radar', 'state'], s) });
  const reprocess = useMutation({
    mutationFn: () => radarApi.reprocess(panelRunId!),
    onSuccess: () => {
      refresh();
      toast({ tone: 'good', title: 'Contas refeitas com o catálogo atual' });
    },
  });
  const run = state.last_run;
  const error = saveKey.error || dropKey.error || simulate.error || collect.error || auto.error || reprocess.error;

  return (
    <Section
      title="Acesso e coleta"
      icon={KeyRound}
      description="O radar lê a YouTube Data API: com uma chave própria ou, sem ela, com o acesso de um canal vinculado em Canais."
      actions={
        <>
          <Button icon={Calculator} busy={simulate.isPending} onClick={() => simulate.mutate()}>
            Simular
          </Button>
          <Button variant="primary" icon={Play} disabled={!state.access.ok} busy={collect.isPending || running} onClick={() => collect.mutate()}>
            {running ? 'Coletando…' : 'Coletar agora'}
          </Button>
        </>
      }
    >
      <div className="flex flex-wrap items-center gap-2 text-sm">
        {state.access.ok ? (
          <Badge tone="good">Acesso: {state.access.source}</Badge>
        ) : (
          <p className="rounded-md border border-amber-300/35 bg-amber-300/10 px-2.5 py-1.5 text-amber-200">{state.access.message}</p>
        )}
        {run ? (
          <span className="text-xs text-muted">
            Última coleta {run.created_at ? formatRelative(run.created_at) : ''}: {run.status === 'ok' ? `${run.videos} vídeos, ${run.channels} canais, ` : 'falhou, '}
            {run.units} unidades de cota{run.error ? ` (${run.error})` : ''}.
          </span>
        ) : (
          <span className="text-xs text-muted">Nenhuma coleta ainda.</span>
        )}
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <Field
          label="Chave da YouTube Data API"
          hint={state.access.key_masked ? `Chave salva: ${state.access.key_masked}` : 'Google Cloud → APIs e serviços → Credenciais → Criar chave de API.'}
          className="min-w-[240px] flex-1"
        >
          <Input type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} placeholder="AIza…" />
        </Field>
        <Button disabled={key.trim().length < 10} busy={saveKey.isPending} onClick={() => saveKey.mutate()}>
          Salvar chave
        </Button>
        {state.access.key_masked ? (
          <Button variant="ghost" icon={Trash2} busy={dropKey.isPending} onClick={() => dropKey.mutate()}>
            Remover
          </Button>
        ) : null}
      </div>
      {sim ? (
        <p className="text-sm text-muted">
          Simulação: <b className="text-fg">{sim.buscas} buscas</b>, cerca de <b className="text-fg">{sim.unidades_previstas.toLocaleString('pt-BR')}</b> unidades
          de cota (orçamento {sim.orcamento.toLocaleString('pt-BR')}; a cota do dia é 10.000).
          {sim.pulados.length ? ` Ficam de fora: ${sim.pulados.join(', ')}.` : ' Todos os nichos e mercados entram.'}
        </p>
      ) : null}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <Checkbox
          label="Rodar toda semana"
          description="Na rotina da manhã, uma vez por semana, se houver menos de 15 ideias novas esperando."
          checked={state.auto}
          disabled={auto.isPending}
          onChange={(v) => auto.mutate(v)}
        />
        <Button size="sm" icon={RefreshCw} disabled={!panelRunId} busy={reprocess.isPending} onClick={() => reprocess.mutate()}>
          Refazer as contas sem gastar cota
        </Button>
        <Button size="sm" variant="ghost" icon={ListChecks} aria-expanded={editing} onClick={() => setEditing((e) => !e)}>
          {editing ? 'Fechar o catálogo' : `Catálogo (${state.catalog.niches} nichos)`}
        </Button>
      </div>
      <ErrorBox error={error} onClose={() => [saveKey, dropKey, simulate, collect, auto, reprocess].forEach((m) => m.reset())} />
      {editing ? <CatalogEditor /> : null}
    </Section>
  );
};
