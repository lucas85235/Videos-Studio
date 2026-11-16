// Painel de criar/editar série.
import { useMutation, useQueryClient } from '@tanstack/react-query';
import React, { useState } from 'react';
import type { Channel, StyleItem } from '../../api';
import { CADENCE_OPTIONS, SERIES_KEY, seriesApi, type Series, type SeriesFormat, type SeriesInput, type SeriesSource } from '../../apiSeries';
import { Button, Checkbox, ErrorBox, Field, FieldGroup, Input, SegmentedControl, Select, Textarea, useToast } from '../../ui';
import { Sheet } from '../ops/common';
import { TODAY_KEY } from '../today/shared';

type Form = {
  channel_id: string;
  name: string;
  theme: string;
  notes: string;
  format: SeriesFormat;
  style_id: string;
  cadence_days: number;
  stock_target: string;
  max_per_run: string;
  source: SeriesSource;
  topics: string;
  idea_filter: string;
  autopilot: boolean;
};

const fromSeries = (s: Series | null, channel: string): Form => ({
  channel_id: s?.channel_id ?? channel,
  name: s?.name ?? '',
  theme: s?.theme ?? '',
  notes: s?.notes ?? '',
  format: s?.format ?? '',
  style_id: s?.style_id ?? '',
  cadence_days: s?.cadence_days ?? 7,
  stock_target: String(s?.stock_target ?? 2),
  max_per_run: String(s?.max_per_run ?? 1),
  source: s?.source ?? 'list',
  topics: (s?.topics ?? []).join('\n'),
  idea_filter: s?.idea_filter ?? '',
  autopilot: s?.autopilot ?? false,
});

const intIn = (text: string, low: number, high: number): number | null => {
  const n = Number(text.trim());
  return Number.isInteger(n) && n >= low && n <= high ? n : null;
};

export const SeriesSheet: React.FC<{
  initial: Series | null;
  channels: Channel[];
  styles: StyleItem[];
  defaultChannel: string;
  onClose: () => void;
}> = ({ initial, channels, styles, defaultChannel, onClose }) => {
  const qc = useQueryClient();
  const { toast } = useToast();
  const [form, setForm] = useState<Form>(() => fromSeries(initial, defaultChannel || channels[0]?.id || ''));
  const [tried, setTried] = useState(false);
  const set = <K extends keyof Form>(key: K, value: Form[K]) => setForm((f) => ({ ...f, [key]: value }));

  const stock = intIn(form.stock_target, 0, 30);
  const perRun = intIn(form.max_per_run, 1, 5);
  const errors = {
    channel: form.channel_id ? undefined : 'Escolha o canal.',
    name: form.name.trim() ? undefined : 'Dê um nome à série.',
    stock: stock === null ? 'De 0 a 30.' : undefined,
    perRun: perRun === null ? 'De 1 a 5.' : undefined,
  };
  const invalid = Object.values(errors).some(Boolean);
  const topics = form.topics
    .split('\n')
    .map((t) => t.trim())
    .filter(Boolean);
  const visibleStyles = styles.filter((s) => !form.format || s.style.format.type === form.format);

  const save = useMutation({
    mutationFn: () => {
      const body: SeriesInput = {
        channel_id: form.channel_id,
        name: form.name.trim(),
        theme: form.theme,
        notes: form.notes,
        format: form.format,
        style_id: form.style_id || null,
        cadence_days: form.cadence_days,
        stock_target: stock ?? 0,
        max_per_run: perRun ?? 1,
        source: form.source,
        topics,
        idea_filter: form.idea_filter,
        autopilot: form.autopilot,
      };
      return initial ? seriesApi.update(initial.id, body) : seriesApi.create(body);
    },
    onSuccess: (out) => {
      qc.invalidateQueries({ queryKey: SERIES_KEY });
      qc.invalidateQueries({ queryKey: TODAY_KEY });
      toast({ tone: 'good', title: initial ? 'Série salva' : 'Série criada', description: out.name });
      onClose();
    },
  });

  const submit = () => {
    setTried(true);
    if (invalid || save.isPending) return;
    save.mutate();
  };

  return (
    <Sheet
      title={initial ? `Editar “${initial.name}”` : 'Nova série'}
      description="Tema, estilo e cadência da série. O piloto cria episódios e para no roteiro para você revisar."
      onRequestClose={onClose}
      onSubmit={submit}
      footer={
        <>
          <Button onClick={onClose}>Cancelar</Button>
          <Button type="submit" variant="primary" busy={save.isPending}>
            {initial ? 'Salvar' : 'Criar série'}
          </Button>
        </>
      }
    >
      <Field label="Canal" error={tried ? errors.channel : undefined}>
        <Select value={form.channel_id} onChange={(e) => set('channel_id', e.target.value)}>
          <option value="">Escolha…</option>
          {channels.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </Select>
      </Field>
      <Field label="Nome da série" error={tried ? errors.name : undefined}>
        <Input value={form.name} maxLength={200} onChange={(e) => set('name', e.target.value)} placeholder="Ex.: Faróis malditos" />
      </Field>
      <Field label="Tema da série" hint="Vai no briefing de todo episódio.">
        <Textarea rows={2} value={form.theme} maxLength={4000} onChange={(e) => set('theme', e.target.value)} placeholder="Ex.: faróis abandonados e as histórias deles" />
      </Field>
      <Field label="Instruções fixas" hint="Estrutura, bordão de abertura, chamada no fim. Opcional.">
        <Textarea rows={2} value={form.notes} maxLength={4000} onChange={(e) => set('notes', e.target.value)} />
      </Field>
      <FieldGroup legend="Formato">
        <SegmentedControl<SeriesFormat>
          value={form.format}
          onChange={(v) => setForm((f) => ({ ...f, format: v, style_id: '' }))}
          options={[
            { id: '', label: 'Qualquer' },
            { id: 'short', label: 'Shorts' },
            { id: 'long', label: 'Longos' },
          ]}
        />
      </FieldGroup>
      <Field label="Estilo" hint="Vazio: o estilo padrão do canal (ou um do formato escolhido).">
        <Select value={form.style_id} onChange={(e) => set('style_id', e.target.value)}>
          <option value="">Padrão do canal</option>
          {visibleStyles.map((s) => (
            <option key={s.style.id} value={s.style.id}>
              {s.style.name}
            </option>
          ))}
        </Select>
      </Field>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Field label="Cadência">
          <Select value={String(form.cadence_days)} onChange={(e) => set('cadence_days', Number(e.target.value))}>
            {CADENCE_OPTIONS.some((o) => o.days === form.cadence_days) ? null : <option value={form.cadence_days}>A cada {form.cadence_days} dias</option>}
            {CADENCE_OPTIONS.map((o) => (
              <option key={o.days} value={o.days}>
                {o.label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Episódios em estoque" hint="À frente, sem publicar." error={tried ? errors.stock : undefined}>
          <Input inputMode="numeric" value={form.stock_target} onChange={(e) => set('stock_target', e.target.value)} />
        </Field>
        <Field label="Máximo por rodada" error={tried ? errors.perRun : undefined}>
          <Input inputMode="numeric" value={form.max_per_run} onChange={(e) => set('max_per_run', e.target.value)} />
        </Field>
      </div>
      <FieldGroup legend="De onde vêm os temas">
        <SegmentedControl<SeriesSource>
          value={form.source}
          onChange={(v) => set('source', v)}
          options={[
            { id: 'list', label: 'Lista de temas' },
            { id: 'ideas', label: 'Ideias aprovadas' },
          ]}
        />
      </FieldGroup>
      {form.source === 'list' ? (
        <Field label={`Fila de temas (${topics.length})`} hint="Um tema por linha. O primeiro vira o próximo episódio e sai da fila.">
          <Textarea rows={6} value={form.topics} onChange={(e) => set('topics', e.target.value)} placeholder={'O farol da ilha Rasa\nO farol de Santa Marta'} />
        </Field>
      ) : (
        <Field label="Palavra que a ideia precisa ter" hint="Opcional. Usa as ideias aprovadas do canal, da nota mais alta para a mais baixa.">
          <Input value={form.idea_filter} maxLength={200} onChange={(e) => set('idea_filter', e.target.value)} placeholder="Ex.: farol" />
        </Field>
      )}
      <Checkbox
        label="Piloto automático"
        description="Toda manhã a rotina completa o estoque da série. Cada episódio para no roteiro esperando você; nada é publicado sozinho."
        checked={form.autopilot}
        onChange={(v) => set('autopilot', v)}
      />
      <ErrorBox error={save.error} title="Não foi possível salvar a série" onClose={() => save.reset()} />
    </Sheet>
  );
};
