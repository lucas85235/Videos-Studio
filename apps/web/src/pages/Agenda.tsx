import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { type Agenda, type AgendaItem, api, formatWhen, PLATFORM_LABELS } from '../api';
import { Badge, Button, Card, Empty, ErrorBox, Input, Spinner } from '../ui';

const dayKey = (iso: string) => new Date(iso).toLocaleDateString('pt-BR', { weekday: 'long', day: '2-digit', month: 'long' });
const timeOf = (iso: string) => new Date(iso).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });

const ItemTitle: React.FC<{ item: AgendaItem }> = ({ item }) => (
  <div className="flex min-w-0 flex-wrap items-center gap-1.5">
    <Badge tone="info">{PLATFORM_LABELS[item.platform] ?? item.platform}</Badge>
    <Link to={`/projetos/${item.project_id}`} className="truncate font-medium text-zinc-100 hover:underline">
      {item.project_title}
    </Link>
    <span className="text-xs text-zinc-500">
      {item.language}
      {item.channel_name ? ` · ${item.channel_name}` : ''}
    </span>
  </div>
);

const ManualItem: React.FC<{ item: AgendaItem }> = ({ item }) => {
  const client = useQueryClient();
  const [url, setUrl] = useState('');
  const open = useMutation({ mutationFn: () => api.post(`/api/publications/${item.id}/open`) });
  const mark = useMutation({
    mutationFn: () => api.post(`/api/publications/${item.id}/published`, { url: url.trim() || null }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['agenda'] }),
  });
  const overdue = item.scheduled_at ? new Date(item.scheduled_at).getTime() < Date.now() : false;
  return (
    <Card className="flex flex-col gap-2 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <ItemTitle item={item} />
        {item.scheduled_at ? (
          <Badge tone={overdue ? 'warn' : 'neutral'}>{overdue ? 'atrasado: ' : 'planejado: '}{formatWhen(item.scheduled_at)}</Badge>
        ) : (
          <Badge>sem horário</Badge>
        )}
      </div>
      {item.caption ? <div className="line-clamp-2 whitespace-pre-wrap text-xs text-zinc-400">{item.caption}</div> : null}
      <div className="flex flex-wrap gap-2 text-xs">
        <Button className="text-xs" busy={open.isPending} onClick={() => open.mutate()}>Abrir pasta</Button>
        {item.upload_url ? (
          <a className="self-center text-sky-400 hover:underline" href={item.upload_url} target="_blank" rel="noreferrer">Página de upload</a>
        ) : null}
        {item.caption ? (
          <button className="self-center text-sky-400 hover:underline" onClick={() => navigator.clipboard.writeText(item.caption ?? '')}>Copiar legenda</button>
        ) : null}
      </div>
      <div className="flex flex-wrap gap-2">
        <Input className="min-w-0 flex-1 text-xs" placeholder="link do post (opcional)" value={url} onChange={(e) => setUrl(e.target.value)} />
        <Button className="text-xs" variant="primary" busy={mark.isPending} onClick={() => mark.mutate()}>Marcar como postado</Button>
      </div>
      <ErrorBox error={open.error ?? mark.error} />
    </Card>
  );
};

export const AgendaPage: React.FC = () => {
  const agenda = useQuery({ queryKey: ['agenda'], queryFn: () => api.get<Agenda>('/api/agenda'), refetchInterval: 60_000 });
  const data = agenda.data;
  const groups = new Map<string, AgendaItem[]>();
  for (const item of data?.upcoming ?? []) {
    const key = dayKey(item.scheduled_at!);
    groups.set(key, [...(groups.get(key) ?? []), item]);
  }
  return (
    <div className="mx-auto max-w-5xl p-6">
      <h1 className="mb-1 text-xl font-bold">Agenda</h1>
      <p className="mb-6 text-sm text-zinc-500">
        Publicações agendadas no YouTube, pacotes para postar à mão (TikTok, Instagram e outras) e o que saiu nas últimas duas semanas. Horários no fuso deste computador.
        Configure os horários de postagem de cada canal em <Link to="/canais" className="text-sky-400 hover:underline">Canais</Link>.
      </p>
      {agenda.isLoading ? (
        <div className="flex items-center gap-2 text-sm text-zinc-500"><Spinner /> carregando…</div>
      ) : null}
      <ErrorBox error={agenda.error} />
      {data ? (
        <div className="flex flex-col gap-8">
          <section>
            <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-zinc-400">Próximas publicações</h2>
            {groups.size === 0 ? (
              <Empty>Nada agendado. Agende pelo cartão do YouTube na aba “Vídeo e publicação” de cada projeto.</Empty>
            ) : (
              <div className="flex flex-col gap-4">
                {[...groups.entries()].map(([day, items]) => (
                  <div key={day}>
                    <div className="mb-1.5 text-xs font-semibold capitalize text-zinc-300">{day}</div>
                    <div className="flex flex-col gap-1.5">
                      {items.map((item) => (
                        <Card key={item.id} className="flex flex-wrap items-center gap-3 py-2 text-sm">
                          <span className="w-12 font-mono text-amber-300">{timeOf(item.scheduled_at!)}</span>
                          <div className="min-w-0 flex-1"><ItemTitle item={item} /></div>
                          {item.url ? <a className="text-xs text-sky-400 hover:underline" href={item.url} target="_blank" rel="noreferrer">abrir</a> : null}
                        </Card>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>
          <section>
            <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-zinc-400">Para postar manualmente</h2>
            {data.manual.length === 0 ? (
              <Empty>Nenhum pacote pendente. Gere um em “Publicar (assistente manual)” na aba “Vídeo e publicação”.</Empty>
            ) : (
              <div className="grid gap-3 md:grid-cols-2">
                {data.manual.map((item) => <ManualItem key={item.id} item={item} />)}
              </div>
            )}
          </section>
          <section>
            <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-zinc-400">Publicadas recentemente</h2>
            {data.recent.length === 0 ? (
              <Empty>Nada publicado nas últimas duas semanas.</Empty>
            ) : (
              <div className="flex flex-col gap-1.5">
                {data.recent.map((item) => {
                  const when = item.scheduled_at ?? item.published_at ?? item.created_at;
                  return (
                    <Card key={item.id} className="flex flex-wrap items-center gap-3 py-2 text-sm">
                      <span className="w-36 text-xs text-zinc-500">{when ? formatWhen(when) : ''}</span>
                      <div className="min-w-0 flex-1"><ItemTitle item={item} /></div>
                      <Badge tone={item.status === 'published' ? 'good' : 'neutral'}>
                        {item.status === 'published' ? 'publicado' : item.privacy === 'private' ? 'enviado (privado)' : item.privacy === 'unlisted' ? 'enviado (não listado)' : 'enviado'}
                      </Badge>
                      {item.url ? <a className="text-xs text-sky-400 hover:underline" href={item.url} target="_blank" rel="noreferrer">abrir</a> : null}
                    </Card>
                  );
                })}
              </div>
            )}
          </section>
        </div>
      ) : null}
    </div>
  );
};
