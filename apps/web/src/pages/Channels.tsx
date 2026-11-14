import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import React, { useState } from 'react';
import { api, type Channel, LANGUAGES, type StyleItem } from '../api';
import { Button, Card, Empty, ErrorBox, Field, Input, Select, Textarea } from '../ui';

const blank = { name: '', niche: '', persona: '', languages: ['pt-BR'], default_style_id: null as string | null };

export const ChannelsPage: React.FC = () => {
  const client = useQueryClient();
  const channels = useQuery({ queryKey: ['channels'], queryFn: () => api.get<Channel[]>('/api/channels') });
  const styles = useQuery({ queryKey: ['styles'], queryFn: () => api.get<StyleItem[]>('/api/styles') });
  const [form, setForm] = useState(blank);
  const [editing, setEditing] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () => (editing ? api.put(`/api/channels/${editing}`, form) : api.post('/api/channels', form)),
    onSuccess: () => {
      setForm(blank);
      setEditing(null);
      client.invalidateQueries({ queryKey: ['channels'] });
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.del(`/api/channels/${id}`),
    onSuccess: () => client.invalidateQueries({ queryKey: ['channels'] }),
  });
  return (
    <div className="mx-auto max-w-5xl p-6">
      <h1 className="mb-1 text-xl font-bold">Canais</h1>
      <p className="mb-6 text-sm text-zinc-500">A persona do canal entra nos roteiros. Um canal por idioma ajuda no modo “um vídeo por idioma”.</p>
      <div className="grid gap-4 md:grid-cols-[1fr_340px]">
        <div className="flex flex-col gap-3">
          {channels.data?.length === 0 ? <Empty>Nenhum canal ainda.</Empty> : null}
          {(channels.data ?? []).map((c) => (
            <Card key={c.id} className="flex items-start justify-between gap-3">
              <div>
                <div className="font-semibold">{c.name}</div>
                <div className="text-xs text-zinc-400">{c.niche} · {c.languages.join(', ')} · estilo padrão: {c.default_style_id ?? '—'}</div>
                {c.persona ? <div className="mt-1 text-xs text-zinc-500">{c.persona}</div> : null}
              </div>
              <div className="flex gap-1">
                <Button variant="ghost" className="text-xs" onClick={() => { setEditing(c.id); setForm({ name: c.name, niche: c.niche, persona: c.persona, languages: c.languages, default_style_id: c.default_style_id }); }}>Editar</Button>
                <Button variant="ghost" className="text-xs" onClick={() => window.confirm('Apagar canal?') && remove.mutate(c.id)}>Apagar</Button>
              </div>
            </Card>
          ))}
        </div>
        <Card className="flex flex-col gap-3">
          <div className="text-sm font-semibold">{editing ? 'Editar canal' : 'Novo canal'}</div>
          <Field label="Nome"><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
          <Field label="Nicho"><Input value={form.niche} onChange={(e) => setForm({ ...form, niche: e.target.value })} placeholder="ex.: mistérios históricos" /></Field>
          <Field label="Persona do narrador"><Textarea rows={3} value={form.persona} onChange={(e) => setForm({ ...form, persona: e.target.value })} placeholder="ex.: narrador sóbrio, curioso, que sempre cita fontes" /></Field>
          <Field label="Idioma principal">
            <Select value={form.languages[0]} onChange={(e) => setForm({ ...form, languages: [e.target.value] })}>
              {LANGUAGES.map((l) => <option key={l.code} value={l.code}>{l.label}</option>)}
            </Select>
          </Field>
          <Field label="Estilo padrão">
            <Select value={form.default_style_id ?? ''} onChange={(e) => setForm({ ...form, default_style_id: e.target.value || null })}>
              <option value="">—</option>
              {(styles.data ?? []).map((s) => <option key={s.style.id} value={s.style.id}>{s.style.name}</option>)}
            </Select>
          </Field>
          <Button variant="primary" disabled={!form.name.trim()} busy={save.isPending} onClick={() => save.mutate()}>Salvar</Button>
          {editing ? <Button variant="ghost" onClick={() => { setEditing(null); setForm(blank); }}>Cancelar</Button> : null}
          <ErrorBox error={save.error} />
        </Card>
      </div>
    </div>
  );
};
