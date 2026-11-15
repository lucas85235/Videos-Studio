import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Bot, Check, ListTodo, Plus, X } from 'lucide-react';
import React, { useState } from 'react';
import { api, type Channel } from '../../api';
import { opsApi, type PendingStatus, type PendingTask } from '../../apiOps';
import { Badge, Button, ErrorBox, errorMessage, Field, IconButton, Input, Section, Select, useToast } from '../../ui';
import { TODAY_KEY, WhereLink } from './shared';

const AddPending: React.FC<{ channels: Channel[] }> = ({ channels }) => {
  const client = useQueryClient();
  const { toast } = useToast();
  const [text, setText] = useState('');
  const [where, setWhere] = useState('');
  const [channelId, setChannelId] = useState('');
  const add = useMutation({
    mutationFn: () => opsApi.createPending({ text: text.trim(), where: where.trim(), channel_id: channelId || null }),
    onSuccess: () => {
      setText('');
      setWhere('');
      client.invalidateQueries({ queryKey: TODAY_KEY });
      toast({ tone: 'good', title: 'Pendência adicionada' });
    },
  });
  return (
    <form
      className="flex flex-col gap-3 rounded-lg border border-line p-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (text.trim()) add.mutate();
      }}
    >
      <div className="grid gap-3 md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)]">
        <Field label="O que fazer">
          <Input value={text} onChange={(e) => setText(e.target.value)} maxLength={2000} placeholder="Ex.: responder os comentários do último vídeo" />
        </Field>
        <Field label="Onde fazer (opcional)" hint="Caminho no app (/agenda) ou link.">
          <Input value={where} onChange={(e) => setWhere(e.target.value)} maxLength={500} placeholder="/agenda" />
        </Field>
        <Field label="Canal (opcional)">
          <Select value={channelId} onChange={(e) => setChannelId(e.target.value)}>
            <option value="">Nenhum</option>
            {channels.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </Select>
        </Field>
      </div>
      <ErrorBox error={add.error} title="Não foi possível adicionar" />
      <div>
        <Button type="submit" variant="primary" size="sm" icon={Plus} busy={add.isPending} disabled={!text.trim()}>
          Adicionar pendência
        </Button>
      </div>
    </form>
  );
};

const PendingRow: React.FC<{ item: PendingTask; channelName?: string }> = ({ item, channelName }) => {
  const client = useQueryClient();
  const { toast } = useToast();
  const set = useMutation({
    mutationFn: (status: PendingStatus) => opsApi.setPendingStatus(item.id, status),
    onSuccess: (_, status) => {
      client.invalidateQueries({ queryKey: TODAY_KEY });
      toast({
        tone: 'good',
        title: status === 'feita' ? 'Marcada como feita' : 'Pendência descartada',
        description: item.text,
        action: { label: 'Desfazer', onClick: () => opsApi.setPendingStatus(item.id, 'aberta').then(() => client.invalidateQueries({ queryKey: TODAY_KEY })) },
      });
    },
    onError: (err) => toast({ tone: 'bad', title: 'Não foi possível alterar', description: errorMessage(err) }),
  });
  return (
    <li className="flex flex-wrap items-center gap-3 px-3 py-2.5">
      <div className="flex min-w-[12rem] flex-1 flex-col gap-0.5">
        <span className="text-sm text-fg">{item.text}</span>
        <span className="flex flex-wrap items-center gap-1.5 text-[13px] text-subtle">
          {item.kind === 'auto' ? (
            <Badge icon={Bot} title="Aberta por uma rotina automática">
              Rotina
            </Badge>
          ) : null}
          {channelName ? <span>{channelName}</span> : null}
        </span>
      </div>
      <WhereLink where={item.where} label="Onde fazer" />
      <div className="flex items-center gap-1">
        <IconButton label="Marcar como feita" icon={Check} size="sm" variant="secondary" busy={set.isPending && set.variables === 'feita'} onClick={() => set.mutate('feita')} />
        <IconButton label="Descartar" icon={X} size="sm" busy={set.isPending && set.variables === 'descartada'} onClick={() => set.mutate('descartada')} />
      </div>
    </li>
  );
};

/** Pendências abertas (à mão ou das rotinas), com adicionar, marcar como feita e descartar. */
export const PendingSection: React.FC<{ items: PendingTask[] }> = ({ items }) => {
  const channels = useQuery({ queryKey: ['channels'], queryFn: () => api.get<Channel[]>('/api/channels') });
  const [adding, setAdding] = useState(false);
  const names = new Map((channels.data ?? []).map((c) => [c.id, c.name]));
  return (
    <Section
      title="Pendências"
      icon={ListTodo}
      description="O que ficou para fazer. As rotinas abrem pendências sozinhas (estoque baixo, números das lojas, análise dos vídeos)."
      actions={
        <Button size="sm" icon={adding ? X : Plus} onClick={() => setAdding((v) => !v)} aria-expanded={adding}>
          {adding ? 'Fechar' : 'Adicionar'}
        </Button>
      }
    >
      {adding ? <AddPending channels={channels.data ?? []} /> : null}
      {items.length === 0 ? (
        <p className="text-sm text-muted">Nenhuma pendência aberta.</p>
      ) : (
        <ul className="flex flex-col divide-y divide-line rounded-lg border border-line">
          {items.map((item) => (
            <PendingRow key={item.id} item={item} channelName={item.channel_id ? names.get(item.channel_id) : undefined} />
          ))}
        </ul>
      )}
    </Section>
  );
};
