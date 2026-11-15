import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Coins, Plus, Trash2 } from 'lucide-react';
import React, { useState } from 'react';
import { businessApi, type RevenueEntry, type RevenueMonth } from '../../apiBusiness';
import { Button, Empty, ErrorBox, Field, IconButton, Input, Section, useConfirm, useToast } from '../../ui';
import { formatAmount, formatCount, monthLabel, parseNumber, refreshMoney } from './common';

const lastMonth = () => {
  const d = new Date();
  d.setDate(1);
  d.setMonth(d.getMonth() - 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
};

const EMPTY = { store: 'amazon', period: '', tracking_id: '', clicks: '', orders: '', sales: '', commission: '', currency: 'BRL', note: '' };
type Form = typeof EMPTY;
const NUMBERS = ['clicks', 'orders', 'sales', 'commission'] as const;
const INTEGERS = new Set<string>(['clicks', 'orders']);
const NUMBER_LABELS: Record<(typeof NUMBERS)[number], string> = { clicks: 'Cliques', orders: 'Pedidos', sales: 'Vendas', commission: 'Comissão' };

const RevenueForm: React.FC<{ channelId: string; onDone: () => void }> = ({ channelId, onDone }) => {
  const qc = useQueryClient();
  const { toast } = useToast();
  const [form, setForm] = useState<Form>({ ...EMPTY, period: lastMonth() });
  const set = <K extends keyof Form>(key: K, value: Form[K]) => setForm((f) => ({ ...f, [key]: value }));
  const values = Object.fromEntries(NUMBERS.map((k) => [k, parseNumber(form[k], INTEGERS.has(k))])) as Record<(typeof NUMBERS)[number], number | null>;
  const invalid = NUMBERS.filter((k) => values[k] !== null && Number.isNaN(values[k]));
  const empty = NUMBERS.every((k) => values[k] === null);
  const periodInvalid = !/^\d{4}-(0[1-9]|1[0-2])$/.test(form.period);
  const save = useMutation({
    mutationFn: () =>
      businessApi.saveRevenue({
        channel_id: channelId,
        store: form.store.trim().toLowerCase(),
        period: form.period,
        tracking_id: form.tracking_id.trim(),
        clicks: values.clicks,
        orders: values.orders,
        sales: values.sales,
        commission: values.commission,
        currency: form.currency.trim().toUpperCase() || 'BRL',
        note: form.note.trim() || null,
      }),
    onSuccess: () => {
      refreshMoney(qc, channelId);
      toast({ tone: 'good', title: 'Números lançados', description: 'Lançar de novo o mesmo mês só troca os números enviados.' });
      onDone();
    },
  });
  return (
    <form
      className="flex flex-col gap-3 rounded-lg border border-line p-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (!invalid.length && !empty && !periodInvalid && form.store.trim()) save.mutate();
      }}
    >
      <div className="grid gap-3 sm:grid-cols-4">
        <Field label="Loja">
          <Input value={form.store} required onChange={(e) => set('store', e.target.value)} placeholder="amazon" />
        </Field>
        <Field label="Mês" error={periodInvalid ? 'Escolha o mês.' : undefined}>
          <Input type="month" value={form.period} onChange={(e) => set('period', e.target.value)} />
        </Field>
        <Field label="ID de rastreamento" hint="Vazio = total da loja.">
          <Input value={form.tracking_id} onChange={(e) => set('tracking_id', e.target.value.replace(/\s/g, ''))} />
        </Field>
        <Field label="Moeda">
          <Input value={form.currency} maxLength={3} onChange={(e) => set('currency', e.target.value.toUpperCase())} />
        </Field>
        {NUMBERS.map((k) => (
          <Field
            key={k}
            label={NUMBER_LABELS[k]}
            error={invalid.includes(k) ? (INTEGERS.has(k) ? 'Número inteiro (ex.: 1.234).' : 'Use vírgula para centavos (ex.: 1.234,56).') : undefined}
            hint={values[k] !== null && !Number.isNaN(values[k]) ? `Vai lançar: ${INTEGERS.has(k) ? formatCount(values[k]) : formatAmount(values[k], form.currency.trim().toUpperCase() || 'BRL')}` : undefined}
          >
            <Input inputMode={INTEGERS.has(k) ? 'numeric' : 'decimal'} value={form[k]} onChange={(e) => set(k, e.target.value)} />
          </Field>
        ))}
      </div>
      <Field label="Nota">
        <Input value={form.note} onChange={(e) => set('note', e.target.value)} />
      </Field>
      {empty ? <p className="text-[13px] text-subtle">Preencha ao menos um número: cliques, pedidos, vendas ou comissão.</p> : null}
      <ErrorBox error={save.error} title="Não foi possível lançar" />
      <div className="flex flex-wrap gap-2">
        <Button type="submit" size="sm" variant="primary" busy={save.isPending} disabled={!!invalid.length || empty || periodInvalid}>
          Lançar
        </Button>
        <Button size="sm" variant="ghost" onClick={onDone}>
          Cancelar
        </Button>
      </div>
    </form>
  );
};

/** Receita por mês e loja (soma por ID de rastreamento, sem contar o total duas vezes) e lançamento dos números. */
export const Revenue: React.FC<{ channelId: string; months: RevenueMonth[]; entries: RevenueEntry[] }> = ({ channelId, months, entries }) => {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [adding, setAdding] = useState(false);
  const remove = useMutation({
    mutationFn: (id: number) => businessApi.deleteRevenue(id),
    onSuccess: () => refreshMoney(qc, channelId),
  });
  return (
    <Section
      title="Receita"
      icon={Coins}
      description="Números que você traz dos painéis das lojas, por mês. Com linhas por ID de rastreamento, o total da loja não é somado de novo."
      actions={
        adding ? null : (
          <Button size="sm" icon={Plus} onClick={() => setAdding(true)}>
            Lançar números
          </Button>
        )
      }
    >
      {adding ? <RevenueForm channelId={channelId} onDone={() => setAdding(false)} /> : null}
      {months.length ? (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] text-sm">
            <caption className="sr-only">Receita por mês e loja</caption>
            <thead>
              <tr className="border-b border-line text-left text-[13px] text-subtle">
                <th className="py-1.5 pr-3 font-medium">Mês</th>
                <th className="py-1.5 pr-3 font-medium">Loja</th>
                <th className="py-1.5 pr-3 text-right font-medium">Cliques</th>
                <th className="py-1.5 pr-3 text-right font-medium">Pedidos</th>
                <th className="py-1.5 pr-3 text-right font-medium">Vendas</th>
                <th className="py-1.5 text-right font-medium">Comissão</th>
              </tr>
            </thead>
            <tbody>
              {months.map((m) => (
                <React.Fragment key={`${m.period}-${m.store}-${m.currency}`}>
                  <tr className="border-b border-line/60">
                    <td className="py-1.5 pr-3 text-fg">{monthLabel(m.period)}</td>
                    <td className="py-1.5 pr-3 text-muted">{m.store}</td>
                    <td className="py-1.5 pr-3 text-right tabular-nums">{formatCount(m.clicks)}</td>
                    <td className="py-1.5 pr-3 text-right tabular-nums">{formatCount(m.orders)}</td>
                    <td className="py-1.5 pr-3 text-right tabular-nums">{formatAmount(m.sales, m.currency)}</td>
                    <td className="py-1.5 text-right font-medium tabular-nums text-fg">{formatAmount(m.commission, m.currency)}</td>
                  </tr>
                  {m.by_tracking.map((t) => (
                    <tr key={t.tracking_id} className="text-[13px] text-subtle">
                      <td />
                      <td className="py-1 pr-3">↳ {t.tracking_id}</td>
                      <td className="py-1 pr-3 text-right tabular-nums">{formatCount(t.clicks)}</td>
                      <td className="py-1 pr-3 text-right tabular-nums">{formatCount(t.orders)}</td>
                      <td className="py-1 pr-3 text-right tabular-nums">{formatAmount(t.sales, m.currency)}</td>
                      <td className="py-1 text-right tabular-nums">{formatAmount(t.commission, m.currency)}</td>
                    </tr>
                  ))}
                </React.Fragment>
              ))}
            </tbody>
          </table>
        </div>
      ) : adding ? null : (
        <Empty title="Sem receita lançada">Lance os números do mês (cliques, pedidos, comissão) que aparecem no painel de cada loja.</Empty>
      )}
      {entries.length ? (
        <details className="text-sm">
          <summary className="w-fit cursor-pointer text-[13px] text-muted hover:text-fg">Lançamentos ({entries.length})</summary>
          <ul className="mt-2 flex flex-col gap-1">
            {entries.map((e) => (
              <li key={e.id} className="flex flex-wrap items-center gap-2 text-[13px] text-muted">
                <span className="text-fg">{e.period}</span>
                <span>{e.store}</span>
                <span>{e.tracking_id || 'total da loja'}</span>
                <span className="tabular-nums">
                  {NUMBERS.filter((k) => e[k] != null)
                    .map((k) => `${NUMBER_LABELS[k].toLowerCase()} ${k === 'sales' || k === 'commission' ? formatAmount(e[k], e.currency) : formatCount(e[k])}`)
                    .join(' · ')}
                </span>
                <IconButton
                  label="Apagar lançamento"
                  icon={Trash2}
                  size="sm"
                  onClick={async () => {
                    if (await confirm({ title: 'Apagar este lançamento?', description: `${e.period} · ${e.store} · ${e.tracking_id || 'total'}`, confirmLabel: 'Apagar', tone: 'danger' }))
                      remove.mutate(e.id);
                  }}
                />
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      <ErrorBox error={remove.error} />
    </Section>
  );
};
