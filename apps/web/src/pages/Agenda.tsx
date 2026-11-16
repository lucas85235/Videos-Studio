import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  CalendarCheck,
  CalendarClock,
  Check,
  CircleCheck,
  Clock,
  Copy,
  ExternalLink,
  FolderOpen,
  Hand,
  History,
  RotateCw,
  Send,
  TriangleAlert,
  Tv,
  Upload,
} from 'lucide-react';
import React, { useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { type Agenda, type AgendaItem, api, type ChannelSlots, PLATFORM_LABELS, type YouTubeBatchResult, type YouTubeReady } from '../api';
import { langLabel } from '../labels';
import { isActiveJob, useJobs } from '../shell/data';
import { Badge, Button, buttonClass, ErrorBox, Field, Input, Page, PageHeader, Section, Skeleton, useConfirm, useToast } from '../ui';
import { PROJECT_TAB_SLUGS } from './project/types';
import { ChipGroup, StatTile, useCopy } from './ops/common';
import { browserZone, dayLabel, timeOf, whenIn, zoneLabel } from './ops/zones';

type YouTubeStatus = { connected: boolean; client_configured?: boolean; channel_title?: string };

/** Limite do servidor por lote (YouTubeBatchBody.items max_length). */
const BATCH_MAX = 50;
/** O servidor calcula no máximo 20 horários livres por consulta (/channels/{id}/slots). */
const SLOTS_MAX = 20;

const ALL = 'todos';
const NO_CHANNEL = 'sem-canal';
const channelKey = (id: string | null | undefined) => id || NO_CHANNEL;

const SECTION = { manual: 'por-postar', queue: 'fila-youtube', upcoming: 'agendadas', recent: 'publicadas' } as const;

const isOverdue = (item: AgendaItem, now: number) => (item.scheduled_at ? new Date(item.scheduled_at).getTime() < now : false);

/** Atrasados primeiro, depois os planejados por horário e por fim os sem horário. */
const sortManual = (items: AgendaItem[], now: number) => {
  const rank = (i: AgendaItem) => (isOverdue(i, now) ? 0 : i.scheduled_at ? 1 : 2);
  const time = (i: AgendaItem) => (i.scheduled_at ? new Date(i.scheduled_at).getTime() : Infinity);
  return [...items].sort((a, b) => rank(a) - rank(b) || time(a) - time(b));
};

const projectVideoTab = (projectId: string) => `/projetos/${projectId}?aba=${PROJECT_TAB_SLUGS.output}`;

const ExternalButton: React.FC<{ href: string; children: React.ReactNode; label?: string }> = ({ href, children, label }) => (
  <a href={href} target="_blank" rel="noreferrer" aria-label={label} className={buttonClass({ variant: 'ghost', size: 'sm' })}>
    <ExternalLink size={14} aria-hidden="true" className="shrink-0" />
    {children}
  </a>
);

const ItemTitle: React.FC<{ item: AgendaItem }> = ({ item }) => (
  <div className="flex min-w-0 flex-col gap-0.5">
    <Link to={`/projetos/${item.project_id}`} className="truncate font-medium text-fg hover:underline" title={item.project_title}>
      {item.project_title}
    </Link>
    <div className="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1 text-[13px] text-subtle">
      <Badge>{PLATFORM_LABELS[item.platform] ?? item.platform}</Badge>
      <span>{langLabel(item.language)}</span>
      {item.channel_name ? <span>· {item.channel_name}</span> : null}
    </div>
  </div>
);

/* ------------------------------------------------------------------ */
/* Pacote manual                                                       */
/* ------------------------------------------------------------------ */

const StepNumber: React.FC<{ n: number }> = ({ n }) => (
  <span aria-hidden="true" className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-line-strong text-xs font-semibold tabular-nums text-muted">
    {n}
  </span>
);

const ManualItem: React.FC<{ item: AgendaItem; now: number }> = ({ item, now }) => {
  const client = useQueryClient();
  const { toast } = useToast();
  const { copied, copy } = useCopy();
  const [url, setUrl] = useState('');
  const [tried, setTried] = useState(false);
  const open = useMutation({ mutationFn: () => api.post(`/api/publications/${item.id}/open`) });
  const mark = useMutation({
    mutationFn: () => api.post(`/api/publications/${item.id}/published`, { url: url.trim() || null }),
    onSuccess: () => {
      toast({ tone: 'good', title: 'Marcado como postado', description: `${item.project_title} · ${PLATFORM_LABELS[item.platform] ?? item.platform}` });
      client.invalidateQueries({ queryKey: ['agenda'] });
    },
  });
  const overdue = isOverdue(item, now);
  const badUrl = Boolean(url.trim()) && !/^https?:\/\//i.test(url.trim());
  const steps: React.ReactNode[] = [
    <Button size="sm" icon={FolderOpen} busy={open.isPending} onClick={() => open.mutate()}>
      Abrir pasta do pacote
    </Button>,
  ];
  if (item.caption) {
    steps.push(
      <Button size="sm" icon={copied === 'legenda' ? Check : Copy} onClick={() => void copy('legenda', item.caption ?? '', 'Legenda copiada', 'Cole na descrição do post.')}>
        {copied === 'legenda' ? 'Legenda copiada' : 'Copiar legenda'}
      </Button>,
    );
  }
  if (item.upload_url) {
    steps.push(
      <a href={item.upload_url} target="_blank" rel="noreferrer" className={buttonClass({ size: 'sm' })}>
        <ExternalLink size={14} aria-hidden="true" className="shrink-0" />
        Abrir página de upload
      </a>,
    );
  }
  return (
    <article
      aria-label={item.project_title}
      className={`flex min-w-0 flex-col gap-3 rounded-xl border bg-card p-4 ${overdue ? 'border-amber-300/45' : 'border-line'}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <ItemTitle item={item} />
        {item.scheduled_at ? (
          overdue ? (
            <Badge tone="warn" icon={TriangleAlert}>
              Atrasado · {whenIn(item.scheduled_at)}
            </Badge>
          ) : (
            <Badge icon={Clock}>Planejado · {whenIn(item.scheduled_at)}</Badge>
          )
        ) : (
          <Badge>Sem horário</Badge>
        )}
      </div>
      <ol aria-label="Passos para postar" className="flex flex-col gap-2">
        {steps.map((step, i) => (
          <li key={i} className="flex items-center gap-2">
            <StepNumber n={i + 1} />
            {step}
          </li>
        ))}
      </ol>
      {item.caption ? (
        <details className="text-[13px] text-muted">
          <summary className="w-fit cursor-pointer rounded-sm hover:text-fg">Ver legenda</summary>
          <p className="mt-2 max-h-48 overflow-y-auto whitespace-pre-wrap break-words rounded-md border border-line bg-canvas p-2.5 text-[13px] text-muted">{item.caption}</p>
        </details>
      ) : null}
      <form
        className="flex flex-col gap-2 border-t border-line pt-3"
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          setTried(true);
          if (badUrl || mark.isPending) return;
          mark.mutate();
        }}
      >
        <div className="flex items-center gap-2 text-[13px] font-medium text-fg">
          <StepNumber n={steps.length + 1} />
          Depois de postar
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <Field label="Link do post (opcional)" error={tried && badUrl ? 'O link precisa começar com http:// ou https://' : undefined} className="min-w-0 flex-1 basis-56">
            <Input type="url" inputMode="url" placeholder="https://…" value={url} onChange={(e) => setUrl(e.target.value)} />
          </Field>
          <Button type="submit" icon={CircleCheck} busy={mark.isPending}>
            Marcar como postado
          </Button>
        </div>
      </form>
      <ErrorBox error={open.error ?? mark.error} onClose={() => (open.error ? open.reset() : mark.reset())} />
    </article>
  );
};

/* ------------------------------------------------------------------ */
/* Fila do YouTube                                                     */
/* ------------------------------------------------------------------ */

/** `short`: o motivo em minúsculas, para o contador do topo ("1 sem canal"). */
type Block = { text: string; short: string; to: string; action: string; why?: string };

const readyKey = (r: YouTubeReady) => `${r.project_id}:${r.lang}`;

/**
 * Horários livres de cada canal com vídeos na fila (um por vídeo do canal) e o bloqueio de cada vídeo.
 * Fica na página para o contador do topo, o texto vazio e a Fila usarem a mesma conta.
 */
const useQueueBlocks = (ready: YouTubeReady[]) => {
  const perChannel = useMemo(() => {
    const counts = new Map<string, number>();
    for (const r of ready) if (r.channel_id && !r.preview_only) counts.set(r.channel_id, (counts.get(r.channel_id) ?? 0) + 1);
    return [...counts.entries()];
  }, [ready]);
  const slotQueries = useQueries({
    queries: perChannel.map(([id, n]) => ({
      queryKey: ['channel-slots', id, 'fila', n],
      queryFn: () => api.get<ChannelSlots>(`/api/channels/${id}/slots?count=${Math.min(n, SLOTS_MAX)}`),
      staleTime: 30_000,
    })),
  });
  const slotsOf = new Map<string, ChannelSlots | undefined>(perChannel.map(([id], i) => [id, slotQueries[i]?.data]));
  const slotErrorOf = new Map<string, boolean>(perChannel.map(([id], i) => [id, Boolean(slotQueries[i]?.isError)]));
  const slotsFailed = slotQueries.some((q) => q.isError);
  const slotsRetrying = slotQueries.some((q) => q.isFetching);
  const retrySlots = () => slotQueries.forEach((q) => void q.refetch());

  const blockOf = (r: YouTubeReady): Block | null => {
    if (!r.channel_id)
      return {
        text: 'Sem canal',
        short: 'sem canal',
        to: projectVideoTab(r.project_id),
        action: 'Escolher data no projeto',
        why: 'O canal é escolhido ao criar o projeto; sem canal não há horários automáticos.',
      };
    if (r.preview_only)
      return { text: 'Feito com voz ou imagens de prévia', short: 'feito em prévia', to: '/configuracoes', action: 'Configurar provedores e renderizar de novo' };
    const slots = slotsOf.get(r.channel_id);
    if (slots && !slots.configured)
      return { text: 'Canal sem horários de postagem', short: 'canal sem horários', to: '/canais', action: 'Definir horários do canal' };
    return null;
  };
  return { slotsOf, slotErrorOf, slotsFailed, slotsRetrying, retrySlots, blockOf };
};

type QueueBlocks = ReturnType<typeof useQueueBlocks>;

const YouTubeQueue: React.FC<{ items: YouTubeReady[]; total: number; loading: boolean; error: unknown; onRetry: () => void; blocks: QueueBlocks }> = ({
  items,
  total,
  loading,
  error,
  onRetry,
  blocks,
}) => {
  const client = useQueryClient();
  const confirm = useConfirm();
  const { toast } = useToast();
  const status = useQuery({ queryKey: ['youtube-status'], queryFn: () => api.get<YouTubeStatus>('/api/youtube/status') });
  const jobs = useJobs();
  const [skip, setSkip] = useState<string[]>([]);
  const { slotsOf, slotErrorOf, slotsFailed, slotsRetrying, retrySlots, blockOf } = blocks;
  const sending = (jobs.data ?? []).filter((j) => j.kind === 'publish_youtube' && isActiveJob(j)).length;

  const usable = items.filter((r) => !blockOf(r));
  const chosen = usable.filter((r) => !skip.includes(readyKey(r)));
  const overLimit = chosen.length > BATCH_MAX;

  // Previsão: o i-ésimo vídeo marcado de um canal ocupa o i-ésimo horário livre dele (mesma ordem do envio).
  const predicted = new Map<string, string | null>();
  const used = new Map<string, number>();
  for (const r of chosen) {
    const id = r.channel_id as string;
    const i = used.get(id) ?? 0;
    used.set(id, i + 1);
    const slots = slotsOf.get(id);
    predicted.set(readyKey(r), slots ? (slots.slots[i] ?? null) : null);
  }

  /** Texto da previsão de um vídeo marcado (ou por que não há previsão). */
  const forecast = (r: YouTubeReady, inDialog = false) => {
    const id = r.channel_id as string;
    const at = predicted.get(readyKey(r));
    if (at) return `previsto: ${whenIn(at)}`;
    if (slotErrorOf.get(id)) return inDialog ? 'definido no envio' : 'previsão indisponível';
    const slots = slotsOf.get(id);
    if (!slots) return inDialog ? 'próximo livre' : 'calculando horário…';
    const position = chosen.filter((x) => x.channel_id === id).indexOf(r);
    return position >= SLOTS_MAX ? 'definido no envio' : 'sem horário livre previsto';
  };

  // O corpo do envio é montado na hora da confirmação: o que foi listado no diálogo é o que vai.
  const batch = useMutation({
    mutationFn: (batchItems: { project_id: string; lang: string }[]) =>
      api.post<{ results: YouTubeBatchResult[] }>('/api/agenda/youtube-batch', { items: batchItems }),
    onSuccess: (data) => {
      setSkip([]);
      const ok = data.results.filter((r) => !r.error).length;
      const failed = data.results.length - ok;
      toast({
        tone: failed ? 'warn' : 'good',
        title: ok ? `${ok} ${ok === 1 ? 'vídeo agendado' : 'vídeos agendados'} no YouTube` : 'Nenhum vídeo foi agendado',
        description: failed ? `${failed} com problema: veja os detalhes na Fila do YouTube.` : 'O envio continua em segundo plano.',
      });
      client.invalidateQueries({ queryKey: ['youtube-ready'] });
      client.invalidateQueries({ queryKey: ['agenda'] });
      client.invalidateQueries({ queryKey: ['channel-slots'] });
    },
  });

  const connected = Boolean(status.data?.connected);
  const clientMissing = status.data?.client_configured === false;
  // Sem conexão, a ação principal da Fila é conectar; "Agendar" fica secundário e desabilitado até lá.
  const needsConnection = Boolean(status.data) && !connected;
  const askBatch = async () => {
    const list = chosen.slice(0, BATCH_MAX);
    const batchItems = list.map((r) => ({ project_id: r.project_id, lang: r.lang }));
    const n = list.length;
    const ok = await confirm({
      title: `Agendar ${n} ${n === 1 ? 'vídeo' : 'vídeos'} no YouTube?`,
      description: (
        <div className="flex flex-col gap-3">
          <p>
            Os vídeos são enviados agora{status.data?.channel_title ? ` para a conta “${status.data.channel_title}”` : ''} e ficam privados até o horário,
            quando são publicados sozinhos. Depois do envio, mudanças só pelo YouTube Studio.
          </p>
          {overLimit ? (
            <p className="text-[13px] text-amber-200">
              O YouTube aceita até {BATCH_MAX} por vez: vão os {BATCH_MAX} primeiros; os outros {chosen.length - BATCH_MAX} ficam marcados para o próximo lote.
            </p>
          ) : null}
          {sending ? (
            <p className="text-[13px] text-amber-200">
              Há {sending} {sending === 1 ? 'envio' : 'envios'} em andamento: os horários podem mudar.
            </p>
          ) : null}
          <ul className="max-h-64 divide-y divide-line overflow-y-auto rounded-lg border border-line">
            {list.map((r) => {
              const at = predicted.get(readyKey(r));
              return (
                <li key={readyKey(r)} className="flex items-start justify-between gap-3 px-3 py-2">
                  <span className="min-w-0">
                    <span className="block truncate text-fg">{r.title}</span>
                    <span className="block text-[13px] text-subtle">
                      {r.channel_name} · {langLabel(r.lang)}
                    </span>
                  </span>
                  <span className="shrink-0 text-right text-[13px] tabular-nums text-fg">{at ? whenIn(at) : forecast(r, true)}</span>
                </li>
              );
            })}
          </ul>
          <p className="text-[13px] text-subtle">Horários previstos no fuso deste computador. O horário final é confirmado no envio.</p>
        </div>
      ),
      confirmLabel: `Agendar ${n} no YouTube`,
      tone: 'primary',
    });
    if (ok) batch.mutate(batchItems);
  };

  const firstProject = items[0] ?? null;
  const allChosen = usable.length > 0 && chosen.length === usable.length;

  return (
    <Section
      title="Fila do YouTube"
      icon={Upload}
      description="Vídeos renderizados que ainda não foram enviados. Os marcados vão, na ordem, para o próximo horário livre do canal de cada um."
      actions={
        usable.length > 1 ? (
          <Button size="sm" variant="ghost" onClick={() => setSkip(allChosen ? usable.map(readyKey) : [])}>
            {allChosen ? 'Desmarcar todos' : 'Marcar todos'}
          </Button>
        ) : null
      }
    >
      {loading ? <Skeleton className="h-16" /> : null}
      <ErrorBox error={error} title="Não foi possível carregar a fila" onRetry={onRetry} />
      {!loading && !error && !items.length && !batch.data ? (
        <p className="text-sm text-muted">
          {total ? 'Nenhum vídeo deste canal esperando envio.' : 'Nenhum vídeo renderizado esperando envio ao YouTube.'}
        </p>
      ) : null}
      {items.length ? (
        <ul className="-mx-2 flex flex-col">
          {items.map((r) => {
            const key = readyKey(r);
            const block = blockOf(r);
            const id = `fila-${key.replace(/[^a-zA-Z0-9-]/g, '-')}`;
            const checked = !block && !skip.includes(key);
            return (
              <li key={key} className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md px-2 py-2 hover:bg-raised/60">
                <input
                  id={id}
                  type="checkbox"
                  aria-describedby={block ? `${id}-motivo` : undefined}
                  disabled={Boolean(block)}
                  checked={checked}
                  onChange={(e) => setSkip((prev) => (e.target.checked ? prev.filter((k) => k !== key) : [...prev, key]))}
                  className="h-4 w-4 shrink-0 accent-accent disabled:cursor-not-allowed"
                />
                <label htmlFor={id} className={`flex min-w-[12rem] flex-1 flex-col ${block ? 'cursor-default' : 'cursor-pointer'}`}>
                  <span className={`truncate text-sm font-medium ${block ? 'text-muted' : 'text-fg'}`}>{r.title}</span>
                  <span className="text-[13px] text-subtle">
                    {langLabel(r.lang)}
                    {r.channel_name ? ` · ${r.channel_name}` : ''}
                  </span>
                </label>
                {block ? (
                  <span id={`${id}-motivo`}>
                    <Badge tone="warn" icon={TriangleAlert} title={block.why}>
                      {block.text}
                    </Badge>
                    {block.why ? <span className="sr-only">. {block.why}</span> : null}
                  </span>
                ) : checked ? (
                  <span className="text-[13px] tabular-nums text-muted" title="Previsão: o horário final é definido no envio">
                    {forecast(r)}
                  </span>
                ) : (
                  <span className="text-[13px] text-subtle">fica para depois</span>
                )}
                {/* Uma ação por linha: a que resolve o bloqueio ou, sem bloqueio, abrir o projeto. */}
                {block ? (
                  <Link to={block.to} aria-label={`${block.action}: ${r.title}`} className={buttonClass({ variant: 'ghost', size: 'sm' })}>
                    {block.action}
                  </Link>
                ) : (
                  <Link to={`/projetos/${r.project_id}`} aria-label={`Abrir o projeto ${r.title}`} title="Abrir projeto" className={buttonClass({ variant: 'ghost', size: 'sm' })}>
                    Abrir
                  </Link>
                )}
              </li>
            );
          })}
        </ul>
      ) : null}
      {status.error && items.length ? (
        <ErrorBox error={status.error} title="Não foi possível verificar a conexão com o YouTube" onRetry={() => status.refetch()} />
      ) : null}
      {needsConnection && items.length ? (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border border-amber-300/35 bg-amber-300/10 px-3 py-2.5 text-[13px] text-amber-200">
          <TriangleAlert size={16} aria-hidden="true" className="shrink-0" />
          {clientMissing ? (
            <>
              <span className="min-w-0 flex-1">Conecte o YouTube em Configurações › Publicação para agendar daqui.</span>
              <Link to="/configuracoes#provedores-publish" className={buttonClass({ variant: 'primary' })}>
                Configurar o YouTube
              </Link>
            </>
          ) : (
            <>
              <span className="min-w-0 flex-1">O YouTube não está conectado. Conecte a conta na aba Vídeo e publicação de um projeto.</span>
              {firstProject ? (
                <Link to={projectVideoTab(firstProject.project_id)} className={buttonClass({ variant: 'primary' })}>
                  Conectar no projeto
                </Link>
              ) : null}
            </>
          )}
        </div>
      ) : null}
      {items.length ? (
        <div className="flex flex-wrap items-center gap-3 border-t border-line pt-3">
          <Button
            variant={needsConnection ? 'secondary' : 'primary'}
            icon={Send}
            disabled={!chosen.length || !connected}
            aria-describedby="fila-agendar-dica"
            busy={batch.isPending}
            onClick={() => void askBatch()}
          >
            {chosen.length
              ? `Agendar ${Math.min(chosen.length, BATCH_MAX)} ${Math.min(chosen.length, BATCH_MAX) === 1 ? 'vídeo' : 'vídeos'}`
              : 'Agendar vídeos'}
          </Button>
          <span id="fila-agendar-dica" className="text-[13px] text-subtle">
            {needsConnection
              ? 'Disponível depois de conectar o YouTube.'
              : overLimit
                ? `Agende até ${BATCH_MAX} por vez: os outros ficam para o próximo lote.`
                : 'Você confere a lista e os horários antes do envio.'}
          </span>
          {slotsFailed ? (
            <Button size="sm" variant="ghost" icon={RotateCw} busy={slotsRetrying} onClick={retrySlots} className="ml-auto">
              Tentar de novo a previsão
            </Button>
          ) : null}
        </div>
      ) : null}
      {batch.data?.results.length ? (
        <ul aria-label="Resultado do agendamento" className="flex flex-col gap-1.5 rounded-lg border border-line bg-canvas p-3">
          {batch.data.results.map((r) => (
            <li key={`${r.project_id}:${r.lang}`} className={`flex items-start gap-2 text-[13px] ${r.error ? 'text-amber-200' : 'text-emerald-300'}`}>
              {r.error ? <TriangleAlert size={14} aria-hidden="true" className="mt-0.5 shrink-0" /> : <CircleCheck size={14} aria-hidden="true" className="mt-0.5 shrink-0" />}
              <span className="min-w-0">
                <span className="text-fg">{r.title ?? 'Vídeo'}</span> ({langLabel(r.lang)}):{' '}
                {r.error ?? `agendado para ${whenIn(r.publish_at!)}; o envio continua em segundo plano.`}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
      <ErrorBox error={batch.error} title="Não foi possível agendar" onClose={() => batch.reset()} />
    </Section>
  );
};

/* ------------------------------------------------------------------ */
/* Página                                                              */
/* ------------------------------------------------------------------ */

const BUFFER_QUEUE_GROUP = 'Na fila do Buffer (sem horário ainda)';

const publishedLabel = (item: AgendaItem) =>
  item.status === 'published'
    ? 'Publicado'
    : item.privacy === 'private'
      ? 'Enviado (privado)'
      : item.privacy === 'unlisted'
        ? 'Enviado (não listado)'
        : 'Enviado';

export const AgendaPage: React.FC = () => {
  const agenda = useQuery({ queryKey: ['agenda'], queryFn: () => api.get<Agenda>('/api/agenda'), refetchInterval: 60_000 });
  const ready = useQuery({ queryKey: ['youtube-ready'], queryFn: () => api.get<YouTubeReady[]>('/api/agenda/youtube-ready') });
  const [params, setParams] = useSearchParams();
  const data = agenda.data;
  const now = Date.now();

  // Canais presentes em qualquer seção (para o filtro).
  const channelOptions = useMemo(() => {
    const names = new Map<string, string>();
    const add = (id: string | null, name: string) => {
      const key = channelKey(id);
      if (!names.has(key)) names.set(key, key === NO_CHANNEL ? 'Sem canal' : name || 'Canal sem nome');
    };
    for (const i of [...(data?.manual ?? []), ...(data?.upcoming ?? []), ...(data?.recent ?? [])]) add(i.channel_id, i.channel_name);
    for (const r of ready.data ?? []) add(r.channel_id, r.channel_name);
    return [...names.entries()].sort((a, b) => (a[0] === NO_CHANNEL ? 1 : b[0] === NO_CHANNEL ? -1 : a[1].localeCompare(b[1], 'pt-BR')));
  }, [data, ready.data]);

  const requested = params.get('canal') ?? ALL;
  const filter = requested === ALL || channelOptions.some(([id]) => id === requested) || !data ? requested : ALL;
  const setFilter = (id: string) =>
    setParams(
      (prev) => {
        const p = new URLSearchParams(prev);
        if (id === ALL) p.delete('canal');
        else p.set('canal', id);
        return p;
      },
      { replace: true },
    );
  const match = (id: string | null | undefined) => filter === ALL || channelKey(id) === filter;

  const manual = sortManual((data?.manual ?? []).filter((i) => match(i.channel_id)), now);
  const upcoming = (data?.upcoming ?? []).filter((i) => match(i.channel_id));
  const recent = (data?.recent ?? []).filter((i) => match(i.channel_id));
  const queue = (ready.data ?? []).filter((r) => match(r.channel_id));
  const overdue = manual.filter((i) => isOverdue(i, now)).length;
  const blocks = useQueueBlocks(ready.data ?? []);
  const queueUsable = queue.filter((r) => !blocks.blockOf(r)).length;
  const queueBlocked = queue.length - queueUsable;
  // Motivo dos bloqueados: com um motivo só, o contador diz qual ("1 sem canal"); com vários, só quantos.
  const blockReasons = new Set(queue.map((r) => blocks.blockOf(r)?.short).filter(Boolean));
  const queueHint = queueBlocked
    ? `${queueUsable} ${queueUsable === 1 ? 'pronto' : 'prontos'} · ${queueBlocked} ${
        blockReasons.size === 1 ? [...blockReasons][0] : queueBlocked === 1 ? 'precisa de ajuste' : 'precisam de ajuste'
      }`
    : queue.length
      ? 'Prontos para agendar'
      : '—';

  const groups = new Map<string, AgendaItem[]>();
  for (const item of upcoming) {
    // Post na fila do Buffer sem horário conhecido: grupo próprio, no fim.
    const key = item.scheduled_at ? dayLabel(item.scheduled_at) : BUFFER_QUEUE_GROUP;
    groups.set(key, [...(groups.get(key) ?? []), item]);
  }
  const next = upcoming.find((i) => i.scheduled_at)?.scheduled_at;

  // Com pacotes por postar, eles vêm primeiro (é o que pede ação); sem nenhum, a seção vazia desce.
  const manualSection = data ? (
    <div id={SECTION.manual}>
      <Section
        title="Para postar à mão"
        icon={Hand}
        description="Pacotes prontos (vídeo, legenda e capa) para as redes sem envio automático. Atrasados aparecem primeiro."
        actions={overdue ? <Badge tone="warn" icon={TriangleAlert}>{overdue} {overdue === 1 ? 'atrasado' : 'atrasados'}</Badge> : null}
      >
        {manual.length === 0 ? (
          <p className="text-sm text-muted">
            {filter !== ALL && data.manual.length
              ? 'Nenhum pacote deste canal para postar.'
              : 'Nenhum pacote para postar. Prepare um em “Outras redes (envio manual)”, na aba Vídeo e publicação do projeto.'}
          </p>
        ) : (
          <div className="grid gap-3 md:grid-cols-2">
            {manual.map((item) => (
              <ManualItem key={item.id} item={item} now={now} />
            ))}
          </div>
        )}
      </Section>
    </div>
  ) : null;

  return (
    <Page>
      <PageHeader
        title="Agenda"
        description="Publicações agendadas no YouTube, pacotes para postar à mão (TikTok, Instagram e outras) e o que saiu nas últimas duas semanas."
        meta={
          <span className="inline-flex items-center gap-1.5">
            <Clock size={14} aria-hidden="true" />
            Horários no fuso deste computador: {zoneLabel(browserZone)}
          </span>
        }
        actions={
          <Link to="/canais" className={buttonClass()}>
            <Tv size={16} aria-hidden="true" className="shrink-0" />
            Horários dos canais
          </Link>
        }
      />

      {agenda.isLoading ? (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4" aria-hidden="true">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-24" />
          ))}
        </div>
      ) : null}
      <ErrorBox error={agenda.error} title="Não foi possível carregar a agenda" onRetry={() => agenda.refetch()} />

      {data ? (
        <div className="flex flex-col gap-6">
          <nav aria-label="Resumo da agenda" className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <StatTile
              target={SECTION.manual}
              icon={overdue ? TriangleAlert : Hand}
              label="Para postar à mão"
              value={manual.length}
              tone={overdue ? 'warn' : 'neutral'}
              hint={overdue ? `${overdue} ${overdue === 1 ? 'atrasado' : 'atrasados'}` : 'TikTok, Instagram e outras'}
            />
            <StatTile
              target={SECTION.upcoming}
              icon={CalendarClock}
              label="Agendadas"
              value={upcoming.length}
              hint={next ? `Próxima: ${dayLabel(next).toLowerCase()}, ${timeOf(next)}` : queueUsable ? 'Agende pela Fila do YouTube' : '—'}
            />
            <StatTile
              target={SECTION.queue}
              icon={Upload}
              label="Na fila do YouTube"
              value={ready.isLoading ? '…' : queue.length}
              tone={queueBlocked ? 'warn' : 'neutral'}
              hint={ready.isLoading ? undefined : queueHint}
            />
            <StatTile
              target={SECTION.recent}
              icon={CalendarCheck}
              label="Publicadas recentemente"
              value={recent.length}
              hint="Nos últimos 14 dias"
            />
          </nav>

          {channelOptions.length > 1 ? (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-[13px] font-medium text-muted">
                Canal
              </span>
              <ChipGroup
                label="Filtrar a agenda por canal"
                value={filter}
                onChange={setFilter}
                options={[{ id: ALL, label: 'Todos' }, ...channelOptions.map(([id, name]) => ({ id, label: name }))]}
              />
            </div>
          ) : null}

          {manual.length ? manualSection : null}

          <div id={SECTION.queue}>
            <YouTubeQueue
              items={queue}
              total={ready.data?.length ?? 0}
              loading={ready.isLoading}
              error={ready.error}
              onRetry={() => ready.refetch()}
              blocks={blocks}
            />
          </div>

          <div id={SECTION.upcoming}>
            <Section title="Próximas publicações" icon={CalendarClock} description="Vídeos já enviados ao YouTube ou ao Buffer, esperando o horário (ou a vez na fila do Buffer).">
              {groups.size === 0 ? (
                <p className="text-sm text-muted">
                  {filter !== ALL && data.upcoming.length
                    ? 'Nada agendado para este canal.'
                    : queueUsable
                      ? 'Nada agendado ainda. Use a Fila do YouTube, acima, para agendar os vídeos prontos.'
                      : 'Nada agendado. Agende pelo cartão do YouTube na aba Vídeo e publicação de cada projeto.'}
                </p>
              ) : (
                <div className="flex flex-col gap-5">
                  {[...groups.entries()].map(([day, items]) => (
                    <div key={day} className="flex flex-col gap-1.5">
                      <h3 className="flex items-baseline gap-2 text-sm font-semibold text-fg">
                        {day}
                        <span className="text-[13px] font-normal text-subtle">
                          {items.length} {items.length === 1 ? 'vídeo' : 'vídeos'}
                        </span>
                      </h3>
                      <ul className="flex flex-col divide-y divide-line rounded-lg border border-line">
                        {items.map((item) => (
                          <li key={item.id} className="flex flex-wrap items-center gap-3 px-3 py-2.5">
                            <span className="w-12 shrink-0 text-sm font-semibold tabular-nums text-fg">
                              {item.scheduled_at ? timeOf(item.scheduled_at) : 'Fila'}
                            </span>
                            <div className="min-w-[12rem] flex-1">
                              <ItemTitle item={item} />
                            </div>
                            {item.url ? (
                              <ExternalButton href={item.url} label={`Ver no YouTube: ${item.project_title}`}>
                                Ver no YouTube
                              </ExternalButton>
                            ) : null}
                          </li>
                        ))}
                      </ul>
                    </div>
                  ))}
                </div>
              )}
            </Section>
          </div>

          {manual.length ? null : manualSection}

          <div id={SECTION.recent}>
            <Section
              title="Publicadas recentemente"
              icon={History}
              description="O que saiu nas últimas duas semanas."
              actions={recent.length ? <Badge icon={CalendarCheck}>{recent.length}</Badge> : null}
            >
              {recent.length === 0 ? (
                <p className="text-sm text-muted">{filter !== ALL && data.recent.length ? 'Nada deste canal nas últimas duas semanas.' : 'Nada publicado nas últimas duas semanas.'}</p>
              ) : (
                <ul className="flex flex-col divide-y divide-line rounded-lg border border-line">
                  {recent.map((item) => {
                    const when = item.scheduled_at ?? item.published_at ?? item.created_at;
                    return (
                      <li key={item.id} className="flex flex-wrap items-center gap-3 px-3 py-2.5">
                        <span className="shrink-0 text-[13px] tabular-nums text-subtle sm:w-32">{when ? whenIn(when) : ''}</span>
                        <div className="min-w-[12rem] flex-1">
                          <ItemTitle item={item} />
                        </div>
                        <Badge tone={item.status === 'published' ? 'good' : 'neutral'} icon={item.status === 'published' ? CircleCheck : undefined}>
                          {publishedLabel(item)}
                        </Badge>
                        {item.url ? (
                          <ExternalButton href={item.url} label={`Abrir post: ${item.project_title}`}>
                            Abrir post
                          </ExternalButton>
                        ) : null}
                      </li>
                    );
                  })}
                </ul>
              )}
            </Section>
          </div>
        </div>
      ) : null}
    </Page>
  );
};
