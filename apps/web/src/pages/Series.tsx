import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronDown, ChevronUp, Pencil, Play, Plus, Repeat, ShieldCheck, Trash2 } from 'lucide-react';
import React, { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, type Channel, type StyleItem } from '../api';
import { cadenceLabel, SERIES_KEY, seriesApi, type EpisodeState, type Series } from '../apiSeries';
import { Badge, type BadgeTone, Button, Card, Empty, ErrorBox, Field, Page, PageHeader, Select, Skeleton, formatRelative, useConfirm, useToast } from '../ui';
import { TODAY_KEY } from './today/shared';
import { SeriesSheet } from './series/SeriesSheet';

const STATE_TONES: Record<EpisodeState, BadgeTone> = {
  producao: 'info',
  revisao: 'warn',
  pronto: 'good',
  falhou: 'bad',
  publicado: 'neutral',
  apagado: 'neutral',
};

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** Episódios de uma série (carregados ao abrir). */
const Episodes: React.FC<{ seriesId: string }> = ({ seriesId }) => {
  const detail = useQuery({ queryKey: [...SERIES_KEY, seriesId], queryFn: () => seriesApi.get(seriesId) });
  if (detail.isLoading) return <Skeleton className="h-16" />;
  if (detail.error) return <ErrorBox error={detail.error} title="Não foi possível carregar os episódios" onRetry={() => detail.refetch()} />;
  const episodes = detail.data?.episodes ?? [];
  if (!episodes.length) return <p className="text-sm text-muted">Nenhum episódio ainda. Clique em Rodar agora para criar o primeiro.</p>;
  return (
    <ul className="flex flex-col divide-y divide-line rounded-lg border border-line">
      {episodes.map((e) => (
        <li key={e.id} className="flex flex-col gap-1 px-3 py-2.5">
          <div className="flex flex-wrap items-center gap-2">
            <span className="w-10 shrink-0 text-[13px] tabular-nums text-subtle">#{e.number}</span>
            {e.project_title ? (
              <Link to={`/projetos/${e.project_id}`} className="min-w-0 flex-1 truncate text-sm text-fg hover:underline">
                {e.project_title}
              </Link>
            ) : (
              <span className="min-w-0 flex-1 truncate text-sm text-muted">{e.topic}</span>
            )}
            <Badge tone={STATE_TONES[e.state]}>{e.state_label}</Badge>
          </div>
          {e.error ? <p className="pl-12 text-[13px] text-red-300">{e.error}</p> : null}
        </li>
      ))}
    </ul>
  );
};

const SeriesCard: React.FC<{ item: Series; onEdit: () => void }> = ({ item, onEdit }) => {
  const qc = useQueryClient();
  const { toast } = useToast();
  const confirm = useConfirm();
  const [open, setOpen] = useState(false);
  const refresh = () => {
    qc.invalidateQueries({ queryKey: SERIES_KEY });
    qc.invalidateQueries({ queryKey: TODAY_KEY });
    qc.invalidateQueries({ queryKey: ['projects'] });
    qc.invalidateQueries({ queryKey: ['ideas'] });
  };
  const run = useMutation({
    mutationFn: (count?: number) => seriesApi.run(item.id, count),
    onSuccess: (out) => {
      refresh();
      setOpen(true);
      const n = out.created.length;
      toast({
        tone: out.error ? 'bad' : n ? 'good' : 'info',
        title: n ? `${plural(n, 'episódio criado', 'episódios criados')}` : 'Nenhum episódio novo',
        description: n ? 'O roteiro é escrito agora e para esperando a sua revisão.' : out.message,
      });
    },
  });
  const toggle = useMutation({
    mutationFn: (patch: { autopilot?: boolean; active?: boolean }) => seriesApi.update(item.id, patch),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: () => seriesApi.remove(item.id),
    onSuccess: () => {
      refresh();
      toast({ tone: 'good', title: 'Série apagada', description: 'Os projetos dos episódios continuam em Projetos.' });
    },
  });
  const askDelete = async () => {
    if (
      await confirm({
        title: `Apagar a série “${item.name}”?`,
        description: 'Apaga a série e a lista de episódios. Os projetos dos episódios continuam em Projetos.',
        confirmLabel: 'Apagar',
        tone: 'danger',
      })
    )
      remove.mutate();
  };

  const c = item.counts;
  const source =
    item.source === 'list'
      ? plural(item.topics.length, 'tema na fila', 'temas na fila')
      : `Ideias aprovadas${item.idea_filter ? ` com “${item.idea_filter}”` : ''}`;
  return (
    <Card className="flex flex-col gap-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h2 className="font-display text-[17px] font-semibold text-fg">{item.name}</h2>
          <p className="text-[13px] text-muted">
            {item.channel_name}
            {item.format ? ` · ${item.format === 'short' ? 'Shorts' : 'Vídeos longos'}` : ''} · {cadenceLabel(item.cadence_days)}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {!item.active ? <Badge>Pausada</Badge> : item.autopilot ? <Badge tone="good">Piloto ligado</Badge> : <Badge>Piloto desligado</Badge>}
          {item.active && item.stock_target > 0 ? (
            item.below ? (
              <Badge tone="warn">Abaixo do estoque</Badge>
            ) : (
              <Badge tone="good">Estoque em dia</Badge>
            )
          ) : null}
        </div>
      </div>
      {item.theme ? <p className="text-sm text-fg">{item.theme}</p> : null}
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-[13px] sm:grid-cols-4">
        <div>
          <dt className="text-subtle">Estoque da série</dt>
          <dd className="font-semibold tabular-nums text-fg">
            {c.open} de {item.stock_target}
          </dd>
        </div>
        <div>
          <dt className="text-subtle">Esperando revisão</dt>
          <dd className="font-semibold tabular-nums text-fg">{c.revisao}</dd>
        </div>
        <div>
          <dt className="text-subtle">Cobertura</dt>
          <dd className="font-semibold tabular-nums text-fg">{plural(item.coverage_days, 'dia', 'dias')}</dd>
        </div>
        <div>
          <dt className="text-subtle">De onde vêm os temas</dt>
          <dd className="text-fg">{source}</dd>
        </div>
      </dl>
      {item.last_run_at ? (
        <p className="text-[13px] text-muted">
          Última rodada {formatRelative(item.last_run_at)}: {item.last_run_message}
        </p>
      ) : null}
      <ErrorBox error={run.error || toggle.error || remove.error} onClose={() => (run.reset(), toggle.reset(), remove.reset())} />
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="primary" icon={Play} busy={run.isPending && run.variables === undefined} disabled={!item.active} onClick={() => run.mutate(undefined)}>
          Rodar agora
        </Button>
        <Button size="sm" icon={Plus} busy={run.isPending && run.variables === 1} disabled={!item.active} onClick={() => run.mutate(1)}>
          +1 episódio
        </Button>
        <Button size="sm" variant="ghost" busy={toggle.isPending} disabled={!item.active} onClick={() => toggle.mutate({ autopilot: !item.autopilot })}>
          {item.autopilot ? 'Desligar piloto' : 'Ligar piloto'}
        </Button>
        <Button size="sm" variant="ghost" onClick={() => toggle.mutate({ active: !item.active })}>
          {item.active ? 'Pausar' : 'Retomar'}
        </Button>
        <Button size="sm" variant="ghost" icon={Pencil} onClick={onEdit}>
          Editar
        </Button>
        <Button size="sm" variant="ghost" icon={Trash2} busy={remove.isPending} onClick={askDelete}>
          Apagar
        </Button>
        <Button
          size="sm"
          variant="ghost"
          icon={open ? ChevronUp : ChevronDown}
          aria-expanded={open}
          className="ml-auto"
          onClick={() => setOpen((v) => !v)}
        >
          {`Episódios (${c.total})`}
        </Button>
      </div>
      {open ? <Episodes seriesId={item.id} /> : null}
    </Card>
  );
};

/** Séries: episódios criados sozinhos a partir de uma fila de temas ou das ideias aprovadas, até a revisão. */
export const SeriesPage: React.FC = () => {
  const [channel, setChannel] = useState('');
  const [editing, setEditing] = useState<Series | 'new' | null>(null);
  const channels = useQuery({ queryKey: ['channels'], queryFn: () => api.get<Channel[]>('/api/channels') });
  const styles = useQuery({ queryKey: ['styles'], queryFn: () => api.get<StyleItem[]>('/api/styles') });
  const series = useQuery({ queryKey: [...SERIES_KEY, 'lista', channel], queryFn: () => seriesApi.list(channel || null) });
  const list = useMemo(() => series.data ?? [], [series.data]);
  const noChannels = channels.data && channels.data.length === 0;

  return (
    <Page>
      <PageHeader
        title="Séries"
        description="Episódios criados sozinhos a partir de uma fila de temas ou das ideias aprovadas, no estilo e na cadência da série."
        actions={
          <Button variant="primary" icon={Plus} disabled={!!noChannels} onClick={() => setEditing('new')}>
            Nova série
          </Button>
        }
      />
      <div className="flex flex-col gap-4">
        <p className="flex items-start gap-2 rounded-lg border border-line bg-card px-3 py-2.5 text-[13px] text-muted">
          <ShieldCheck size={16} aria-hidden="true" className="mt-0.5 shrink-0 text-emerald-300" />
          O piloto nunca publica: cada episódio para no roteiro esperando a sua revisão (depois, nas cenas). Com o piloto ligado, a rotina da manhã completa o
          estoque da série.
        </p>
        <Field label="Canal" className="w-56">
          <Select value={channel} onChange={(e) => setChannel(e.target.value)}>
            <option value="">Todos os canais</option>
            {(channels.data ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </Select>
        </Field>
        <ErrorBox error={series.error || channels.error} onRetry={() => series.refetch()} />
        {series.isLoading ? (
          <div className="flex flex-col gap-3">
            <Skeleton className="h-40" />
            <Skeleton className="h-40" />
          </div>
        ) : list.length ? (
          <ul className="flex flex-col gap-3">
            {list.map((item) => (
              <li key={item.id}>
                <SeriesCard item={item} onEdit={() => setEditing(item)} />
              </li>
            ))}
          </ul>
        ) : (
          <Empty
            icon={Repeat}
            title={channel ? 'Nenhuma série neste canal' : 'Nenhuma série ainda'}
            action={
              noChannels ? (
                <Link to="/canais" className="text-sm font-medium text-accent hover:underline">
                  Criar um canal primeiro
                </Link>
              ) : (
                <Button icon={Plus} onClick={() => setEditing('new')}>
                  Nova série
                </Button>
              )
            }
          >
            Uma série tem tema, estilo e cadência. O piloto mantém alguns episódios à frente, sempre parando na revisão.
          </Empty>
        )}
      </div>
      {editing ? (
        <SeriesSheet
          initial={editing === 'new' ? null : editing}
          channels={channels.data ?? []}
          styles={styles.data ?? []}
          defaultChannel={channel}
          onClose={() => setEditing(null)}
        />
      ) : null}
    </Page>
  );
};
