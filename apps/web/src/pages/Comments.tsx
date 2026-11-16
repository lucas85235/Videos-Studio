import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCheck, ExternalLink, Eye, EyeOff, HelpCircle, MessageSquare, MessagesSquare, RefreshCw, Send, Sparkles, TriangleAlert } from 'lucide-react';
import React, { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api, type Channel } from '../api';
import { commentsApi, type CommentFilter, type CommentsQuota, type InboxState, type YouTubeComment } from '../apiComments';
import { Badge, Button, Empty, ErrorBox, formatRelative, Page, PageHeader, SegmentedControl, Select, Skeleton, Textarea, useConfirm, useToast } from '../ui';
import { TODAY_KEY } from './today/shared';

const FILTERS: { id: CommentFilter; label: string }[] = [
  { id: 'nao_respondidos', label: 'Não respondidos' },
  { id: 'perguntas', label: 'Perguntas novas' },
  { id: 'nao_lidos', label: 'Não lidos' },
  { id: 'todos', label: 'Todos' },
];
const isFilter = (v: string | null): v is CommentFilter => FILTERS.some((f) => f.id === v);

const STATE_TEXT: Record<InboxState['state'], string> = {
  ok: 'em dia',
  sem_escopo: 'sem permissão de comentários',
  sem_token: 'sem acesso ao YouTube (vincule em Canais)',
  publico: 'ligado só pelo link público, que não lê comentários (vincule com o login do Google em Canais)',
  erro: 'a última leitura falhou',
  nunca: 'ainda não lido',
};

const KEY = ['comments'] as const;
const PAGE = 100;
const MAX = 500; // limite da rota

/** Avisos por canal: falta de permissão pede novo vínculo, como em Desempenho. */
const InboxNotices: React.FC<{ states: InboxState[] }> = ({ states }) => {
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
            para responder comentários pelo Vulto ({missing.map((s) => s.name).join(', ')}). A leitura continua; o acesso atual não tem a permissão de responder.
          </p>
        ) : null}
        {others.map((s) => (
          <p key={s.channel_id} className="text-muted">
            {s.name}: {STATE_TEXT[s.state]}
            {s.state === 'erro' && s.error ? ` (${s.error.slice(0, 200)})` : ''}.
          </p>
        ))}
      </div>
    </div>
  );
};

const QuotaLine: React.FC<{ quota: CommentsQuota }> = ({ quota }) => (
  <p className="text-[13px] text-muted">
    Cota do YouTube hoje: <span className="tabular-nums text-fg">{quota.comments.toLocaleString('pt-BR')}</span> unidades nos comentários,{' '}
    <span className="tabular-nums text-fg">{quota.youtube.toLocaleString('pt-BR')}</span> de {quota.daily.toLocaleString('pt-BR')} no total contado
    pelo Vulto. Ler gasta {quota.list_cost} por página de 100; cada resposta gasta {quota.reply_cost}.
  </p>
);

/** Um comentário: texto, sugestão da IA, resposta (só depois de confirmar) e lido. */
const CommentCard: React.FC<{ comment: YouTubeComment; channelName?: string; onChanged: () => void }> = ({ comment, channelName, onChanged }) => {
  const { toast } = useToast();
  const confirm = useConfirm();
  const [draft, setDraft] = useState(comment.suggestion);
  useEffect(() => setDraft(comment.suggestion), [comment.suggestion]);

  const suggest = useMutation({
    mutationFn: () => commentsApi.suggest(comment.id),
    onSuccess: (c) => {
      setDraft(c.suggestion);
      if (!c.suggestion) toast({ tone: 'info', title: 'A IA não sugeriu resposta', description: 'Parece spam ou golpe; responda só se quiser.' });
      onChanged();
    },
  });
  const read = useMutation({ mutationFn: (value: boolean) => commentsApi.markRead([comment.id], value), onSuccess: onChanged });
  const send = useMutation({
    mutationFn: (text: string) => commentsApi.reply(comment.id, text),
    onSuccess: () => {
      toast({ tone: 'good', title: 'Resposta publicada no YouTube' });
      onChanged();
    },
  });

  const onSend = async () => {
    const text = draft.trim();
    if (!text) return;
    const ok = await confirm({
      title: 'Responder no YouTube?',
      description: (
        <div className="flex flex-col gap-2">
          <p>A resposta fica pública no vídeo, em nome do canal{channelName ? ` ${channelName}` : ''}. Gasta 50 unidades da cota do dia.</p>
          <blockquote className="whitespace-pre-wrap rounded-md border border-line bg-canvas p-2 text-fg">{text}</blockquote>
        </div>
      ),
      confirmLabel: 'Publicar resposta',
      tone: 'primary',
    });
    if (ok) send.mutate(text);
  };

  return (
    <li className={`flex flex-col gap-2 rounded-lg border p-3 ${comment.read ? 'border-line bg-card' : 'border-line-strong bg-raised/40'}`}>
      <div className="flex flex-wrap items-center gap-2 text-[13px]">
        <span className="font-medium text-fg">{comment.author || 'Anônimo'}</span>
        {comment.published_at ? <span className="text-subtle">{formatRelative(comment.published_at)}</span> : null}
        {comment.is_question ? (
          <Badge tone="info" icon={HelpCircle}>
            Pergunta
          </Badge>
        ) : null}
        {comment.answered ? <Badge tone="good">Respondido</Badge> : null}
        {!comment.read ? <Badge tone="accent">Novo</Badge> : null}
        {comment.like_count ? <span className="text-subtle tabular-nums">{comment.like_count} curtidas</span> : null}
      </div>
      <p className="whitespace-pre-wrap text-sm text-fg">{comment.text}</p>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px] text-muted">
        <span className="min-w-0 truncate">
          Vídeo: {comment.video_title || comment.video_id || 'canal'}
          {comment.reply_count ? ` · ${comment.reply_count} ${comment.reply_count === 1 ? 'resposta' : 'respostas'}` : ''}
        </span>
        {comment.comment_url ? (
          <a href={comment.comment_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-accent hover:underline">
            Abrir no YouTube
            <ExternalLink size={13} aria-hidden="true" />
          </a>
        ) : null}
      </div>
      {comment.reply_text ? (
        <p className="rounded-md border border-line bg-canvas p-2 text-[13px] text-muted">
          Sua resposta pelo Vulto{comment.replied_at ? ` (${formatRelative(comment.replied_at)})` : ''}: <span className="text-fg">{comment.reply_text}</span>
        </p>
      ) : null}
      {comment.can_reply ? (
        <div className="flex flex-col gap-2">
          <Textarea
            aria-label={`Resposta para ${comment.author || 'o comentário'}`}
            rows={2}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Escreva a resposta ou peça uma sugestão à IA"
          />
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" icon={Sparkles} onClick={() => suggest.mutate()} busy={suggest.isPending}>
              {draft ? 'Sugerir outra' : 'Sugerir resposta'}
            </Button>
            <Button size="sm" variant="primary" icon={Send} onClick={onSend} busy={send.isPending} disabled={!draft.trim()}>
              Responder no YouTube
            </Button>
            <Button size="sm" variant="ghost" icon={comment.read ? EyeOff : Eye} onClick={() => read.mutate(!comment.read)} busy={read.isPending}>
              {comment.read ? 'Marcar como não lido' : 'Marcar como lido'}
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[13px] text-subtle">O YouTube não deixa responder este comentário.</span>
          <Button size="sm" variant="ghost" icon={comment.read ? EyeOff : Eye} onClick={() => read.mutate(!comment.read)} busy={read.isPending}>
            {comment.read ? 'Marcar como não lido' : 'Marcar como lido'}
          </Button>
        </div>
      )}
      <ErrorBox error={suggest.error} title="A IA não sugeriu" onClose={() => suggest.reset()} />
      <ErrorBox error={send.error} title="A resposta não foi enviada" onClose={() => send.reset()} />
      <ErrorBox error={read.error} onClose={() => read.reset()} />
    </li>
  );
};

/** Caixa de comentários: comentários dos vídeos do canal, filtros, sugestão da IA e resposta confirmada. */
export const CommentsPage: React.FC = () => {
  const client = useQueryClient();
  const { toast } = useToast();
  const [params, setParams] = useSearchParams();
  const channelId = params.get('canal') ?? '';
  const filterParam = params.get('filtro');
  const filter: CommentFilter = isFilter(filterParam) ? filterParam : 'nao_respondidos';
  const videoId = params.get('video') ?? '';
  const [limit, setLimit] = useState(PAGE);
  useEffect(() => setLimit(PAGE), [channelId, filter, videoId]);

  const setParam = (key: string, value: string) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (value) next.set(key, value);
        else next.delete(key);
        if (key === 'canal') next.delete('video');
        return next;
      },
      { replace: true },
    );

  const channels = useQuery({ queryKey: ['channels'], queryFn: () => api.get<Channel[]>('/api/channels') });
  const list = useQuery({
    queryKey: [...KEY, channelId, filter, videoId, limit],
    queryFn: () => commentsApi.list({ channelId: channelId || null, filter, videoId: videoId || null, limit }),
    placeholderData: (prev) => prev,
  });
  const changed = () => {
    client.invalidateQueries({ queryKey: KEY });
    client.invalidateQueries({ queryKey: TODAY_KEY });
  };

  const refresh = useMutation({
    mutationFn: () => commentsApi.refresh(channelId || null),
    onSuccess: (res) => {
      changed();
      const failed = res.channels.filter((c) => c.state !== 'ok');
      if (!res.channels.length) toast({ tone: 'info', title: 'Nenhum canal ligado ao YouTube', description: 'Vincule um canal em Canais.' });
      else if (failed.length === res.channels.length) toast({ tone: 'bad', title: 'Não deu para ler os comentários', description: failed.map((c) => `${c.name}: ${c.error || c.state}`).join(' · ') });
      else toast({ tone: 'good', title: res.new ? `${res.new} comentário(s) novo(s)` : 'Nenhum comentário novo', description: `${res.units} unidade(s) de cota.` });
    },
  });
  const readAll = useMutation({
    mutationFn: () => commentsApi.markAllRead(channelId || null, filter, videoId || null),
    onSuccess: (res) => {
      changed();
      toast({ tone: 'info', title: res.changed ? `${res.changed} marcado(s) como lido(s)` : 'Nada para marcar' });
    },
  });

  const data = list.data;
  const names = Object.fromEntries((channels.data ?? []).map((c) => [c.id, c.name]));
  const lastRun = (data?.channels ?? [])
    .map((s) => s.last_run)
    .filter((x): x is string => Boolean(x))
    .sort()
    .pop();

  return (
    <Page width="narrow">
      <PageHeader
        title="Comentários"
        description="Comentários dos vídeos do canal. A IA sugere a resposta no tom da persona; nada vai ao YouTube sem você confirmar."
        meta={lastRun ? <span>Última leitura {formatRelative(lastRun)}</span> : <span>Nenhuma leitura ainda</span>}
        actions={
          <>
            <Select aria-label="Canal" value={channelId} onChange={(e) => setParam('canal', e.target.value)} className="w-auto min-w-44">
              <option value="">Todos os canais</option>
              {(channels.data ?? []).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </Select>
            <Button variant="primary" icon={RefreshCw} onClick={() => refresh.mutate()} busy={refresh.isPending}>
              Ler comentários novos
            </Button>
          </>
        }
      />
      <ErrorBox error={refresh.error} title="Não deu para ler os comentários" className="mb-4" onClose={() => refresh.reset()} />
      {data ? <InboxNotices states={data.channels} /> : null}
      {list.isLoading ? (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-10" />
          <Skeleton className="h-32" />
          <Skeleton className="h-32" />
        </div>
      ) : null}
      <ErrorBox error={list.error} onRetry={() => list.refetch()} />
      {data ? (
        <div className="flex flex-col gap-4">
          <div className="flex flex-wrap items-center gap-2">
            <SegmentedControl
              label="Filtro"
              options={FILTERS.map((f) => ({ ...f, count: data.counts[f.id] }))}
              value={filter}
              onChange={(id) => setParam('filtro', id)}
            />
            {data.videos.length > 1 ? (
              <Select aria-label="Vídeo" value={videoId} onChange={(e) => setParam('video', e.target.value)} className="w-auto min-w-44 max-w-72">
                <option value="">Todos os vídeos</option>
                {data.videos.map((v) => (
                  <option key={v.video_id} value={v.video_id}>
                    {v.title} ({v.count})
                  </option>
                ))}
              </Select>
            ) : null}
            <Button variant="ghost" size="sm" icon={CheckCheck} onClick={() => readAll.mutate()} busy={readAll.isPending} disabled={!data.counts.nao_lidos}>
              Marcar todos como lidos
            </Button>
          </div>
          <QuotaLine quota={data.quota} />
          {data.items.length === 0 ? (
            <Empty icon={data.counts.todos ? MessageSquare : MessagesSquare} title={data.counts.todos ? 'Nada neste filtro' : 'Nenhum comentário lido ainda'}>
              {data.counts.todos
                ? 'Troque o filtro para ver os outros comentários.'
                : data.channels.length
                  ? 'Clique em “Ler comentários novos” para trazer os comentários dos vídeos do canal.'
                  : 'Vincule um canal ao YouTube em Canais (login do Google) para ler os comentários.'}
            </Empty>
          ) : (
            <ul className="flex flex-col gap-3">
              {data.items.map((c) => (
                <CommentCard key={c.id} comment={c} channelName={names[c.channel_id]} onChanged={changed} />
              ))}
            </ul>
          )}
          {data.total > data.items.length ? (
            <div className="flex flex-wrap items-center gap-3 text-[13px] text-muted">
              <span>
                Mostrando {data.items.length} de {data.total}.
                {limit >= MAX ? ' Use os filtros (canal, vídeo) para ver o resto.' : ''}
              </span>
              {limit < MAX ? (
                <Button size="sm" variant="ghost" onClick={() => setLimit((n) => Math.min(MAX, n + PAGE))} busy={list.isFetching}>
                  Carregar mais
                </Button>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </Page>
  );
};
