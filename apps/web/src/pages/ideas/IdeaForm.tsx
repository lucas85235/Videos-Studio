import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus } from 'lucide-react';
import React, { useState } from 'react';
import { ApiError, type Channel } from '../../api';
import { businessApi, type IdeaFormat } from '../../apiBusiness';
import { Button, ErrorBox, Field, Input, Section, Select, Textarea, useToast } from '../../ui';

const EMPTY = { title: '', channel_id: '', format: '' as IdeaFormat, angle: '', evidence: '', search: '', score: '' };

/** Formulário "Nova ideia". Título repetido no mesmo canal pede confirmação antes de criar outra. */
export const IdeaForm: React.FC<{ channels: Channel[]; defaultChannel: string; onDone: () => void }> = ({ channels, defaultChannel, onDone }) => {
  const qc = useQueryClient();
  const { toast } = useToast();
  const [form, setForm] = useState({ ...EMPTY, channel_id: defaultChannel });
  const [duplicate, setDuplicate] = useState<string | null>(null);
  const set = <K extends keyof typeof form>(key: K, value: (typeof form)[K]) => {
    setForm((f) => ({ ...f, [key]: value }));
    if (key === 'title' || key === 'channel_id') setDuplicate(null);
  };
  const score = form.score.trim() === '' ? null : Number(form.score.replace(',', '.'));
  const scoreInvalid = score !== null && (Number.isNaN(score) || score < 0 || score > 10);

  const create = useMutation({
    mutationFn: (force: boolean) =>
      businessApi.createIdea({
        title: form.title.trim(),
        channel_id: form.channel_id || null,
        format: form.format,
        angle: form.angle.trim(),
        evidence: form.evidence.trim(),
        search: form.search.trim(),
        score,
        force,
      }),
    onSuccess: (idea) => {
      qc.invalidateQueries({ queryKey: ['ideas'] });
      toast({ tone: 'good', title: 'Ideia guardada', description: idea.title });
      setForm({ ...EMPTY, channel_id: form.channel_id });
      setDuplicate(null);
      onDone();
    },
    onError: (err) => {
      if (err instanceof ApiError && err.status === 409) setDuplicate(err.message);
    },
  });

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.title.trim() || scoreInvalid) return;
    create.mutate(false);
  };

  return (
    <Section title="Nova ideia" description="Guarde a ideia com nota, ângulo e evidência. Dá para virar projeto depois.">
      <form onSubmit={submit} className="flex flex-col gap-3">
        <Field label="Título">
          <Input value={form.title} maxLength={300} required onChange={(e) => set('title', e.target.value)} placeholder="Ex.: Por que o GTA 6 atrasou?" />
        </Field>
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Canal">
            <Select value={form.channel_id} onChange={(e) => set('channel_id', e.target.value)}>
              <option value="">Sem canal</option>
              {channels.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Formato">
            <Select value={form.format} onChange={(e) => set('format', e.target.value as IdeaFormat)}>
              <option value="">Qualquer</option>
              <option value="short">Short</option>
              <option value="long">Vídeo longo</option>
            </Select>
          </Field>
          <Field label="Nota (0 a 10)" error={scoreInvalid ? 'Use um número de 0 a 10.' : undefined}>
            <Input inputMode="decimal" value={form.score} onChange={(e) => set('score', e.target.value)} placeholder="Ex.: 8,5" />
          </Field>
        </div>
        <Field label="Ângulo" hint="O jeito de contar que faz a ideia ser diferente.">
          <Textarea rows={2} value={form.angle} onChange={(e) => set('angle', e.target.value)} />
        </Field>
        <Field label="Evidência" hint="Por que deve funcionar: vídeos parecidos, buscas, comentários.">
          <Textarea rows={2} value={form.evidence} onChange={(e) => set('evidence', e.target.value)} />
        </Field>
        <Field label="Busca" hint="O que foi pesquisado para achar a ideia (opcional).">
          <Input value={form.search} onChange={(e) => set('search', e.target.value)} />
        </Field>
        {duplicate ? (
          <div role="alert" className="flex flex-wrap items-center gap-3 rounded-lg border border-amber-300/35 bg-amber-300/10 p-3 text-sm text-amber-100">
            <span className="min-w-0 flex-1">{duplicate}.</span>
            <Button size="sm" onClick={() => create.mutate(true)} busy={create.isPending}>
              Criar mesmo assim
            </Button>
          </div>
        ) : (
          <ErrorBox error={create.error} title="Não foi possível guardar a ideia" />
        )}
        <div className="flex flex-wrap gap-2">
          <Button type="submit" variant="primary" icon={Plus} busy={create.isPending} disabled={!form.title.trim() || scoreInvalid}>
            Guardar ideia
          </Button>
          <Button variant="ghost" onClick={onDone}>
            Cancelar
          </Button>
        </div>
      </form>
    </Section>
  );
};
