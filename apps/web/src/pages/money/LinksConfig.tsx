import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Link2, Plus, Save, Trash2 } from 'lucide-react';
import React, { useEffect, useState } from 'react';
import { businessApi, type Monetization } from '../../apiBusiness';
import { Button, ErrorBox, Field, IconButton, Input, Section, Textarea, useToast } from '../../ui';
import { refreshMoney } from './common';

type Form = Omit<Monetization, 'fronts' | 'plan' | 'tracking_ids' | 'max_links'> & { max_links: string; ids: { target: string; id: string }[] };

const toForm = (c: Monetization): Form => ({
  since: c.since,
  amazon_tag: c.amazon_tag,
  amazon_domain: c.amazon_domain,
  max_links: String(c.max_links),
  publi: c.publi,
  publi_pinned: c.publi_pinned,
  disclosure: c.disclosure,
  ids: Object.entries(c.tracking_ids).map(([target, id]) => ({ target, id })),
});

/** Configuração dos links na descrição: ID da Amazon, domínio, textos (#publi, comentário fixado, aviso) e IDs por vídeo. */
export const LinksConfig: React.FC<{ channelId: string; config: Monetization; projectTitles: Map<string, string> }> = ({
  channelId,
  config,
  projectTitles,
}) => {
  const qc = useQueryClient();
  const { toast } = useToast();
  const [form, setForm] = useState<Form>(() => toForm(config));
  const [dirty, setDirty] = useState(false);
  useEffect(() => {
    if (!dirty) setForm(toForm(config));
  }, [config, dirty]);
  const set = <K extends keyof Form>(key: K, value: Form[K]) => {
    setForm((f) => ({ ...f, [key]: value }));
    setDirty(true);
  };
  const maxLinks = Number(form.max_links);
  const maxInvalid = !Number.isInteger(maxLinks) || maxLinks < 1 || maxLinks > 10;
  const sinceInvalid = form.since !== '' && !/^\d{4}-(0[1-9]|1[0-2])$/.test(form.since);

  const save = useMutation({
    mutationFn: () =>
      businessApi.saveMonetization(channelId, {
        since: form.since.trim(),
        amazon_tag: form.amazon_tag.trim(),
        amazon_domain: form.amazon_domain.trim() || 'www.amazon.com.br',
        max_links: maxLinks,
        publi: form.publi.trim() || config.publi,
        publi_pinned: form.publi_pinned.trim(),
        disclosure: form.disclosure.trim(),
        tracking_ids: Object.fromEntries(form.ids.filter((r) => r.target.trim() && r.id.trim()).map((r) => [r.target.trim(), r.id.trim()])),
      }),
    onSuccess: () => {
      refreshMoney(qc, channelId);
      setDirty(false);
      toast({ tone: 'good', title: 'Configuração dos links salva' });
    },
  });

  return (
    <Section title="Links na descrição" icon={Link2} description="Como os links de afiliado entram no topo da descrição, no comentário fixado e no fim.">
      <form
        className="flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (!maxInvalid && !sinceInvalid) save.mutate();
        }}
      >
        <div className="grid gap-3 sm:grid-cols-4">
          <Field label="ID de rastreamento padrão" hint="O da loja na Amazon, ex.: meucanal-20.">
            <Input value={form.amazon_tag} onChange={(e) => set('amazon_tag', e.target.value.replace(/\s/g, ''))} />
          </Field>
          <Field label="Domínio da Amazon">
            <Input value={form.amazon_domain} onChange={(e) => set('amazon_domain', e.target.value.trim().toLowerCase())} />
          </Field>
          <Field label="Máximo de links" error={maxInvalid ? 'De 1 a 10.' : undefined}>
            <Input inputMode="numeric" value={form.max_links} onChange={(e) => set('max_links', e.target.value)} />
          </Field>
          <Field label="Links no ar desde" hint="Mês AAAA-MM." error={sinceInvalid ? 'Use AAAA-MM.' : undefined}>
            <Input value={form.since} placeholder="2026-11" onChange={(e) => set('since', e.target.value.trim())} />
          </Field>
        </div>
        <Field label="Texto #publi (topo da descrição)">
          <Input value={form.publi} maxLength={300} onChange={(e) => set('publi', e.target.value)} />
        </Field>
        <Field label="Texto do comentário fixado" hint="Vazio usa o texto #publi.">
          <Input value={form.publi_pinned} maxLength={300} onChange={(e) => set('publi_pinned', e.target.value)} />
        </Field>
        <Field label="Aviso legal (última linha)" hint="Entra no fim da descrição quando há link da Amazon.">
          <Textarea rows={2} value={form.disclosure} maxLength={500} onChange={(e) => set('disclosure', e.target.value)} />
        </Field>
        <fieldset className="m-0 flex min-w-0 flex-col gap-2 border-0 p-0">
          <legend className="mb-1 p-0 text-sm font-medium text-fg">IDs por vídeo</legend>
          <p className="text-[13px] text-subtle">
            Crie o ID em Associados › Gerenciar IDs de rastreamento e anote aqui para medir cada vídeo sozinho. Também dá para cadastrar no painel
            “Links de afiliado” do próprio projeto.
          </p>
          {form.ids.map((r, i) => (
            <div key={i} className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] sm:items-end">
              <Field label="Projeto (id)" hint={projectTitles.get(r.target)}>
                <Input value={r.target} onChange={(e) => set('ids', form.ids.map((x, j) => (j === i ? { ...x, target: e.target.value.trim() } : x)))} />
              </Field>
              <Field label="ID de rastreamento">
                <Input value={r.id} onChange={(e) => set('ids', form.ids.map((x, j) => (j === i ? { ...x, id: e.target.value.replace(/\s/g, '') } : x)))} />
              </Field>
              <IconButton label="Tirar este ID" icon={Trash2} onClick={() => set('ids', form.ids.filter((_, j) => j !== i))} />
            </div>
          ))}
          <Button size="sm" icon={Plus} className="w-fit" onClick={() => set('ids', [...form.ids, { target: '', id: '' }])}>
            Adicionar ID
          </Button>
        </fieldset>
        <ErrorBox error={save.error} title="Não foi possível salvar" />
        <div>
          <Button type="submit" variant="primary" icon={Save} busy={save.isPending} disabled={!dirty || maxInvalid || sinceInvalid}>
            Salvar configuração
          </Button>
        </div>
      </form>
    </Section>
  );
};
