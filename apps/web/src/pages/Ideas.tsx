import { useQuery } from '@tanstack/react-query';
import { Lightbulb, Plus, Telescope } from 'lucide-react';
import React, { useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api, type Channel, DEFAULT_STYLE_ID, type StyleItem } from '../api';
import { businessApi, IDEA_STATUS_LABELS, type IdeaStatus } from '../apiBusiness';
import { Button, buttonClass, Empty, ErrorBox, Field, Input, Page, PageHeader, SegmentedControl, Select, Skeleton } from '../ui';
import { IdeaCard } from './ideas/IdeaCard';
import { IdeaForm } from './ideas/IdeaForm';

type StatusFilter = IdeaStatus | 'todas' | 'abertas';
const STATUS_FILTERS: { id: StatusFilter; label: string }[] = [
  { id: 'abertas', label: 'Para decidir' },
  { id: 'aprovada', label: 'Aprovadas' },
  { id: 'em_producao', label: IDEA_STATUS_LABELS.em_producao },
  { id: 'feita', label: 'Feitas' },
  { id: 'rejeitada', label: 'Rejeitadas' },
  { id: 'descartada', label: 'Descartadas' },
  { id: 'todas', label: 'Todas' },
];

const statusFrom = (v: string | null): StatusFilter | null => STATUS_FILTERS.find((f) => f.id === v)?.id ?? null;

/** Ideias: banco de ideias de vídeo, por nota, com decisão e "Virar projeto". */
export const IdeasPage: React.FC = () => {
  // ?canal= e ?situacao= abrem já filtrado (links da pesquisa de vídeos e de Hoje), também com a página já aberta
  // (o aviso "Ver em Ideias" de outro canal só troca a URL).
  const [params] = useSearchParams();
  const urlChannel = params.get('canal');
  const urlStatus = params.get('situacao');
  const [channel, setChannel] = useState(() => urlChannel ?? '');
  const [status, setStatus] = useState<StatusFilter>(() => statusFrom(urlStatus) ?? 'abertas');
  useEffect(() => {
    if (urlChannel !== null) setChannel(urlChannel);
  }, [urlChannel]);
  useEffect(() => {
    const s = statusFrom(urlStatus);
    if (s) setStatus(s);
  }, [urlStatus]);
  const [minScore, setMinScore] = useState('');
  const [adding, setAdding] = useState(false);

  const channels = useQuery({ queryKey: ['channels'], queryFn: () => api.get<Channel[]>('/api/channels') });
  const styles = useQuery({ queryKey: ['styles'], queryFn: () => api.get<StyleItem[]>('/api/styles') });
  const min = minScore.trim() === '' ? null : Number(minScore.replace(',', '.'));
  const minValid = min === null || (!Number.isNaN(min) && min >= 0 && min <= 10);
  const ideas = useQuery({
    queryKey: ['ideas', channel, minValid ? min : null],
    queryFn: () => businessApi.ideas({ channel_id: channel || null, min_score: minValid ? min : null }),
  });

  const names = useMemo(() => new Map((channels.data ?? []).map((c) => [c.id, c.name])), [channels.data]);
  const all = ideas.data ?? [];
  const counts = useMemo(() => {
    const out: Record<string, number> = { todas: all.length, abertas: 0 };
    for (const i of all) {
      out[i.status] = (out[i.status] ?? 0) + 1;
      if (i.status === 'nova') out.abertas += 1;
    }
    return out;
  }, [all]);
  const visible = all.filter((i) => (status === 'todas' ? true : status === 'abertas' ? i.status === 'nova' : i.status === status));
  const defaultStyle = (id: string | null) => channels.data?.find((c) => c.id === id)?.default_style_id || DEFAULT_STYLE_ID;

  return (
    <Page>
      <PageHeader
        title="Ideias"
        description="Ideias de vídeo com nota, ângulo e evidência. As de nota mais alta ficam no topo."
        actions={
          adding ? null : (
            <Button variant="primary" icon={Plus} onClick={() => setAdding(true)}>
              Nova ideia
            </Button>
          )
        }
      />
      <div className="flex flex-col gap-4">
        {adding ? <IdeaForm channels={channels.data ?? []} defaultChannel={channel} onDone={() => setAdding(false)} /> : null}

        <div className="flex flex-wrap items-end gap-3">
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
          <Field label="Nota mínima" className="w-32" error={minValid ? undefined : 'De 0 a 10.'}>
            <Input inputMode="decimal" value={minScore} onChange={(e) => setMinScore(e.target.value)} placeholder="Ex.: 7" />
          </Field>
          {channel ? (
            <Link to={`/pesquisa?canal=${channel}`} className={buttonClass({ variant: 'ghost' })}>
              <Telescope size={16} strokeWidth={2} aria-hidden="true" className="shrink-0" />
              Pesquisar ideias para este canal
            </Link>
          ) : null}
        </div>
        <SegmentedControl
          label="Situação"
          value={status}
          onChange={setStatus}
          options={STATUS_FILTERS.map((f) => ({ ...f, count: counts[f.id] ?? 0 }))}
        />

        <ErrorBox error={ideas.error || channels.error} onRetry={() => ideas.refetch()} />
        {ideas.isLoading ? (
          <div className="flex flex-col gap-3">
            <Skeleton className="h-28" />
            <Skeleton className="h-28" />
          </div>
        ) : visible.length ? (
          <ul className="flex flex-col gap-3">
            {visible.map((idea) => (
              <li key={idea.id}>
                <IdeaCard
                  idea={idea}
                  channelName={idea.channel_id ? (names.get(idea.channel_id) ?? null) : null}
                  styles={styles.data ?? []}
                  defaultStyle={defaultStyle(idea.channel_id)}
                />
              </li>
            ))}
          </ul>
        ) : (
          <Empty
            icon={Lightbulb}
            title={all.length ? 'Nenhuma ideia neste filtro' : 'Nenhuma ideia ainda'}
            action={
              adding ? null : (
                <Button icon={Plus} onClick={() => setAdding(true)}>
                  Nova ideia
                </Button>
              )
            }
          >
            {all.length ? 'Troque a situação ou a nota mínima para ver as outras.' : 'Guarde aqui as ideias de vídeo e decida quais viram projeto.'}
          </Empty>
        )}
      </div>
    </Page>
  );
};
