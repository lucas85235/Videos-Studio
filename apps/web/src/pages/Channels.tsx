import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import React, { useState } from 'react';
import { api, type Channel, type ChannelSlots, formatWhen, LANGUAGES, type PostingPlan, type StyleItem, TIMEZONES, WEEKDAYS } from '../api';
import { Button, Card, Empty, ErrorBox, Field, Input, Select, Textarea } from '../ui';

const browserZone = (() => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'America/Sao_Paulo';
  } catch {
    return 'America/Sao_Paulo';
  }
})();

type Form = { name: string; niche: string; persona: string; languages: string[]; default_style_id: string | null; posting: PostingPlan };

const blank = (): Form => ({
  name: '',
  niche: '',
  persona: '',
  languages: ['pt-BR'],
  default_style_id: null,
  posting: { timezone: browserZone, slots: [] },
});

const describeSlots = (plan: PostingPlan) =>
  plan.slots.length
    ? plan.slots
        .map((s) => `${s.weekdays.length === 7 ? 'todo dia' : s.weekdays.map((d) => WEEKDAYS[d]).join('/')} ${s.time}`)
        .join(' · ') + ` (${plan.timezone})`
    : 'sem horários de postagem';

const PostingEditor: React.FC<{ value: PostingPlan; onChange: (p: PostingPlan) => void }> = ({ value, onChange }) => {
  const zones = TIMEZONES.includes(value.timezone) ? TIMEZONES : [value.timezone, ...TIMEZONES];
  const setSlot = (index: number, patch: Partial<PostingPlan['slots'][number]>) =>
    onChange({ ...value, slots: value.slots.map((s, i) => (i === index ? { ...s, ...patch } : s)) });
  return (
    <div className="flex flex-col gap-2">
      <Field label="Fuso dos horários">
        <Select value={value.timezone} onChange={(e) => onChange({ ...value, timezone: e.target.value })}>
          {zones.map((z) => <option key={z} value={z}>{z}</option>)}
        </Select>
      </Field>
      {value.slots.map((slot, index) => (
        <div key={index} className="flex flex-col gap-1.5 rounded-lg border border-zinc-800 p-2">
          <div className="flex items-center gap-2">
            <Input type="time" className="w-28 text-xs" value={slot.time} onChange={(e) => setSlot(index, { time: e.target.value })} />
            <button type="button" className="ml-auto text-xs text-zinc-500 hover:text-red-300" onClick={() => onChange({ ...value, slots: value.slots.filter((_, i) => i !== index) })}>
              remover
            </button>
          </div>
          <div className="flex flex-wrap gap-1">
            {WEEKDAYS.map((label, day) => {
              const on = slot.weekdays.includes(day);
              return (
                <button
                  key={day}
                  type="button"
                  onClick={() => {
                    const next = on ? slot.weekdays.filter((d) => d !== day) : [...slot.weekdays, day].sort();
                    if (next.length) setSlot(index, { weekdays: next });
                  }}
                  className={`rounded-md border px-1.5 py-0.5 text-[11px] ${on ? 'border-amber-400 bg-amber-400/10 text-amber-200' : 'border-zinc-700 text-zinc-500'}`}
                >
                  {label}
                </button>
              );
            })}
          </div>
        </div>
      ))}
      <Button className="text-xs" onClick={() => onChange({ ...value, slots: [...value.slots, { weekdays: [0, 1, 2, 3, 4, 5, 6], time: '18:00' }] })}>
        + horário de postagem
      </Button>
    </div>
  );
};

const NextSlots: React.FC<{ channelId: string }> = ({ channelId }) => {
  const slots = useQuery({ queryKey: ['channel-slots', channelId], queryFn: () => api.get<ChannelSlots>(`/api/channels/${channelId}/slots?count=3`) });
  if (!slots.data?.configured) return null;
  return (
    <div className="mt-1 text-xs text-zinc-500">
      Próximos livres: {slots.data.slots.map((s) => formatWhen(s)).join(' · ') || '—'}
    </div>
  );
};

export const ChannelsPage: React.FC = () => {
  const client = useQueryClient();
  const channels = useQuery({ queryKey: ['channels'], queryFn: () => api.get<Channel[]>('/api/channels') });
  const styles = useQuery({ queryKey: ['styles'], queryFn: () => api.get<StyleItem[]>('/api/styles') });
  const [form, setForm] = useState<Form>(blank);
  const [editing, setEditing] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () => (editing ? api.put(`/api/channels/${editing}`, form) : api.post('/api/channels', form)),
    onSuccess: () => {
      setForm(blank());
      setEditing(null);
      client.invalidateQueries({ queryKey: ['channels'] });
      client.invalidateQueries({ queryKey: ['channel-slots'] });
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.del(`/api/channels/${id}`),
    onSuccess: () => client.invalidateQueries({ queryKey: ['channels'] }),
  });
  return (
    <div className="mx-auto max-w-5xl p-6">
      <h1 className="mb-1 text-xl font-bold">Canais</h1>
      <p className="mb-6 text-sm text-zinc-500">
        A persona do canal entra nos roteiros. Um canal por idioma ajuda no modo “um vídeo por idioma”. Os horários de postagem viram sugestões de agendamento
        e aparecem na Agenda.
      </p>
      <div className="grid gap-4 md:grid-cols-[1fr_360px]">
        <div className="flex flex-col gap-3">
          {channels.data?.length === 0 ? <Empty>Nenhum canal ainda.</Empty> : null}
          {(channels.data ?? []).map((c) => (
            <Card key={c.id} className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="font-semibold">{c.name}</div>
                <div className="text-xs text-zinc-400">{c.niche} · {c.languages.join(', ')} · estilo padrão: {c.default_style_id ?? '—'}</div>
                {c.persona ? <div className="mt-1 text-xs text-zinc-500">{c.persona}</div> : null}
                <div className="mt-1 text-xs text-zinc-400">{describeSlots(c.posting)}</div>
                <NextSlots channelId={c.id} />
              </div>
              <div className="flex gap-1">
                <Button
                  variant="ghost"
                  className="text-xs"
                  onClick={() => {
                    setEditing(c.id);
                    setForm({ name: c.name, niche: c.niche, persona: c.persona, languages: c.languages, default_style_id: c.default_style_id, posting: c.posting });
                  }}
                >
                  Editar
                </Button>
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
          <div className="text-xs font-semibold text-zinc-300">Horários de postagem</div>
          <PostingEditor value={form.posting} onChange={(posting) => setForm({ ...form, posting })} />
          <Button variant="primary" disabled={!form.name.trim()} busy={save.isPending} onClick={() => save.mutate()}>Salvar</Button>
          {editing ? <Button variant="ghost" onClick={() => { setEditing(null); setForm(blank()); }}>Cancelar</Button> : null}
          <ErrorBox error={save.error} />
        </Card>
      </div>
    </div>
  );
};
