import { useMutation, useQueryClient } from '@tanstack/react-query';
import { CalendarCheck, Copy, Pencil, Plus, ShoppingBag, Trash2 } from 'lucide-react';
import React, { useState } from 'react';
import { type AffiliateOffer, businessApi, type MoneyOffer, OFFER_STATUS_LABELS, type OfferStatus, todayIso } from '../../apiBusiness';
import { Badge, type BadgeTone, Button, Empty, ErrorBox, Field, IconButton, Input, Section, Select, Textarea, useConfirm, useToast } from '../../ui';
import { useCopy } from '../ops/common';
import { refreshMoney } from './common';

const TONE: Record<OfferStatus, BadgeTone> = { ideia: 'neutral', ativa: 'good', pausada: 'warn', descartada: 'neutral' };

type Form = { slug: string; name: string; store: string; asin: string; link: string; text: string; topics: string; status: OfferStatus; checked_on: string; note: string };
const EMPTY: Form = { slug: '', name: '', store: 'amazon', asin: '', link: '', text: '', topics: '', status: 'ideia', checked_on: '', note: '' };
const toForm = (o: AffiliateOffer): Form => ({ ...o, topics: o.topics.join(', ') });
const slugify = (s: string) =>
  s
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 60);

const OfferForm: React.FC<{ channelId: string; offer: AffiliateOffer | null; onDone: () => void }> = ({ channelId, offer, onDone }) => {
  const qc = useQueryClient();
  const { toast } = useToast();
  const [form, setForm] = useState<Form>(offer ? toForm(offer) : EMPTY);
  const [slugTouched, setSlugTouched] = useState(!!offer);
  const set = <K extends keyof Form>(key: K, value: Form[K]) => setForm((f) => ({ ...f, [key]: value }));
  const save = useMutation({
    mutationFn: () => {
      const body = {
        ...form,
        slug: form.slug.trim(),
        name: form.name.trim(),
        store: form.store.trim().toLowerCase() || 'amazon',
        topics: form.topics.split(',').map((t) => t.trim()).filter(Boolean),
      };
      return offer ? businessApi.updateOffer(channelId, offer.id, body) : businessApi.createOffer(channelId, body);
    },
    onSuccess: () => {
      refreshMoney(qc, channelId);
      toast({ tone: 'good', title: offer ? 'Oferta salva' : 'Oferta criada' });
      onDone();
    },
  });
  const slugInvalid = form.slug !== '' && !/^[a-z0-9][a-z0-9-]*$/.test(form.slug);
  const isAmazon = (form.store.trim().toLowerCase() || 'amazon') === 'amazon';
  return (
    <form
      className="flex flex-col gap-3 rounded-lg border border-line p-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (form.slug && form.name && !slugInvalid) save.mutate();
      }}
    >
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Nome">
          <Input
            value={form.name}
            required
            onChange={(e) => {
              set('name', e.target.value);
              if (!slugTouched) set('slug', slugify(e.target.value));
            }}
            placeholder="Ex.: GTA 6 (PS5)"
          />
        </Field>
        <Field label="Apelido (id)" hint="Letras minúsculas, números e hífen." error={slugInvalid ? 'Use só a-z, 0-9 e hífen.' : undefined}>
          <Input
            value={form.slug}
            required
            onChange={(e) => {
              setSlugTouched(true);
              set('slug', e.target.value.toLowerCase());
            }}
          />
        </Field>
        <Field label="Situação">
          <Select value={form.status} onChange={(e) => set('status', e.target.value as OfferStatus)}>
            {(Object.keys(OFFER_STATUS_LABELS) as OfferStatus[]).map((s) => (
              <option key={s} value={s}>
                {OFFER_STATUS_LABELS[s]}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Loja">
          <Input value={form.store} onChange={(e) => set('store', e.target.value)} placeholder="amazon" />
        </Field>
        {isAmazon ? (
          <Field label="ASIN" hint="Código do produto na Amazon (monta o link com o ID do vídeo).">
            <Input value={form.asin} onChange={(e) => set('asin', e.target.value.toUpperCase().trim())} />
          </Field>
        ) : null}
        <Field label="Link" hint={isAmazon ? 'Link curto da SiteStripe (opcional com ASIN).' : undefined}>
          <Input value={form.link} onChange={(e) => set('link', e.target.value.trim())} placeholder="https://" />
        </Field>
      </div>
      <Field label="Linha da descrição" hint='Ex.: "🎮 GTA 6 em caixa (PS5)". Fica "linha: link".'>
        <Input value={form.text} maxLength={300} onChange={(e) => set('text', e.target.value)} />
      </Field>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Temas" hint='Separados por vírgula. "sempre" entra em todo vídeo.'>
          <Input value={form.topics} onChange={(e) => set('topics', e.target.value)} placeholder="sempre, compra" />
        </Field>
        <Field label="Conferida em" hint="Vendedor, estoque e preço conferidos na página.">
          <Input type="date" value={form.checked_on} onChange={(e) => set('checked_on', e.target.value)} />
        </Field>
      </div>
      <Field label="Nota">
        <Textarea rows={2} value={form.note} onChange={(e) => set('note', e.target.value)} />
      </Field>
      <ErrorBox error={save.error} title="Não foi possível salvar a oferta" />
      <div className="flex flex-wrap gap-2">
        <Button type="submit" size="sm" variant="primary" busy={save.isPending} disabled={!form.slug || !form.name || slugInvalid}>
          {offer ? 'Salvar oferta' : 'Criar oferta'}
        </Button>
        <Button size="sm" variant="ghost" onClick={onDone}>
          Cancelar
        </Button>
      </div>
    </form>
  );
};

/** Ofertas de afiliado do canal: criar, editar, mudar a situação e marcar como conferida hoje. */
export const Offers: React.FC<{ channelId: string; offers: MoneyOffer[] }> = ({ channelId, offers }) => {
  const qc = useQueryClient();
  const { toast } = useToast();
  const confirm = useConfirm();
  const { copied, copy } = useCopy();
  const [editing, setEditing] = useState<string | 'new' | null>(null);
  const patch = useMutation({
    mutationFn: (p: { id: string; body: { checked_on?: string; status?: OfferStatus } }) => businessApi.updateOffer(channelId, p.id, p.body),
    onSuccess: () => refreshMoney(qc, channelId),
  });
  const remove = useMutation({
    mutationFn: (id: string) => businessApi.deleteOffer(channelId, id),
    onSuccess: () => {
      refreshMoney(qc, channelId);
      toast({ tone: 'good', title: 'Oferta apagada' });
    },
  });

  return (
    <Section
      title="Ofertas"
      icon={ShoppingBag}
      description="O que o canal divulga. Só ofertas ativas entram nos vídeos; conferida há mais de 3 dias pede nova conferência."
      actions={
        editing === 'new' ? null : (
          <Button size="sm" icon={Plus} onClick={() => setEditing('new')}>
            Nova oferta
          </Button>
        )
      }
    >
      {editing === 'new' ? <OfferForm channelId={channelId} offer={null} onDone={() => setEditing(null)} /> : null}
      {offers.length ? (
        <ul className="flex flex-col gap-2">
          {offers.map((o) =>
            editing === o.id ? (
              <li key={o.id}>
                <OfferForm channelId={channelId} offer={o} onDone={() => setEditing(null)} />
              </li>
            ) : (
              <li key={o.id} className="flex flex-col gap-2 rounded-lg border border-line p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium text-fg">{o.name}</span>
                  <span className="text-[13px] text-subtle">{o.slug}</span>
                  <Badge tone={TONE[o.status]}>{OFFER_STATUS_LABELS[o.status]}</Badge>
                  <Badge tone={o.stale ? 'warn' : 'good'} title="Vendedor, estoque e preço">
                    {o.checked_on ? (o.stale ? `Conferir (${o.checked_on})` : `Conferida ${o.checked_on}`) : 'Nunca conferida'}
                  </Badge>
                  <span className="text-[13px] text-subtle">{o.store}</span>
                  <div className="ml-auto flex items-center gap-1">
                    <Select
                      aria-label={`Situação de ${o.name}`}
                      value={o.status}
                      className="h-8 w-auto py-0 text-[13px]"
                      onChange={(e) => patch.mutate({ id: o.id, body: { status: e.target.value as OfferStatus } })}
                    >
                      {(Object.keys(OFFER_STATUS_LABELS) as OfferStatus[]).map((s) => (
                        <option key={s} value={s}>
                          {OFFER_STATUS_LABELS[s]}
                        </option>
                      ))}
                    </Select>
                    <Button size="sm" variant="ghost" icon={CalendarCheck} onClick={() => patch.mutate({ id: o.id, body: { checked_on: todayIso() } })}>
                      Conferida hoje
                    </Button>
                    <IconButton label={`Editar ${o.name}`} icon={Pencil} size="sm" onClick={() => setEditing(o.id)} />
                    <IconButton
                      label={`Apagar ${o.name}`}
                      icon={Trash2}
                      size="sm"
                      onClick={async () => {
                        if (await confirm({ title: 'Apagar esta oferta?', description: o.name, confirmLabel: 'Apagar', tone: 'danger' })) remove.mutate(o.id);
                      }}
                    />
                  </div>
                </div>
                {o.url ? (
                  <div className="flex min-w-0 items-center gap-2 text-[13px]">
                    <code className="min-w-0 truncate rounded bg-canvas px-1.5 py-0.5 text-muted" title={o.url}>
                      {o.url}
                    </code>
                    <IconButton label="Copiar link" icon={Copy} size="sm" onClick={() => copy(o.id, o.url, copied === o.id ? 'Copiado de novo' : 'Link copiado')} />
                  </div>
                ) : (
                  <p className="text-[13px] text-amber-200">Sem link: informe o link ou o ASIN com o ID de rastreamento padrão.</p>
                )}
                {o.topics.length ? <p className="text-[13px] text-subtle">Temas: {o.topics.join(', ')}</p> : null}
                {o.note ? <p className="text-[13px] text-muted">{o.note}</p> : null}
              </li>
            ),
          )}
        </ul>
      ) : editing !== 'new' ? (
        <Empty title="Nenhuma oferta ainda">Cadastre os produtos que o canal divulga com link de afiliado.</Empty>
      ) : null}
      <ErrorBox error={patch.error || remove.error} />
    </Section>
  );
};
