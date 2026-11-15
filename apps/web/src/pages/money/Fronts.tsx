import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, Save, Target, Trash2 } from 'lucide-react';
import React, { useEffect, useState } from 'react';
import { businessApi, FRONT_STATUS_LABELS, type Front, type FrontStatus, type Monetization } from '../../apiBusiness';
import { Badge, type BadgeTone, Button, Empty, ErrorBox, Field, IconButton, Input, Section, Select, useToast } from '../../ui';
import { refreshMoney } from './common';

const TONE: Record<FrontStatus, BadgeTone> = {
  ideia: 'neutral',
  preparando: 'info',
  ativa: 'good',
  bloqueada: 'bad',
  pausada: 'warn',
  descartada: 'neutral',
};

const blank = (): Front => ({ id: '', name: '', status: 'ideia', next: '', requirement: '' });

/** Frentes de monetização: como o canal ganha dinheiro além do AdSense, com o próximo passo de cada uma. */
export const Fronts: React.FC<{ channelId: string; config: Monetization }> = ({ channelId, config }) => {
  const qc = useQueryClient();
  const { toast } = useToast();
  const [editing, setEditing] = useState(false);
  const [rows, setRows] = useState<Front[]>(config.fronts);
  const [plan, setPlan] = useState(config.plan);
  useEffect(() => {
    if (!editing) {
      setRows(config.fronts);
      setPlan(config.plan);
    }
  }, [config, editing]);

  const save = useMutation({
    mutationFn: () => businessApi.saveMonetization(channelId, { plan: plan.trim(), fronts: rows.filter((r) => r.name.trim()) }),
    onSuccess: () => {
      refreshMoney(qc, channelId);
      setEditing(false);
      toast({ tone: 'good', title: 'Frentes salvas' });
    },
  });
  const update = (i: number, patch: Partial<Front>) => setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  return (
    <Section
      title="Frentes"
      icon={Target}
      description="Como o canal ganha dinheiro além do AdSense e o que falta para cada frente andar."
      actions={
        editing ? null : (
          <Button size="sm" onClick={() => setEditing(true)}>
            Editar
          </Button>
        )
      }
    >
      {editing ? (
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate();
          }}
        >
          <Field label="Plano" hint="Resumo do plano de monetização do canal (opcional).">
            <Input value={plan} onChange={(e) => setPlan(e.target.value)} />
          </Field>
          {rows.map((r, i) => (
            <div key={i} className="grid gap-2 rounded-lg border border-line p-3 sm:grid-cols-[minmax(0,2fr)_140px_minmax(0,2fr)_minmax(0,1.5fr)_auto] sm:items-end">
              <Field label="Frente">
                <Input value={r.name} onChange={(e) => update(i, { name: e.target.value })} placeholder="Ex.: Afiliado Amazon" />
              </Field>
              <Field label="Situação">
                <Select value={r.status} onChange={(e) => update(i, { status: e.target.value as FrontStatus })}>
                  {(Object.keys(FRONT_STATUS_LABELS) as FrontStatus[]).map((s) => (
                    <option key={s} value={s}>
                      {FRONT_STATUS_LABELS[s]}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Próximo passo">
                <Input value={r.next} onChange={(e) => update(i, { next: e.target.value })} />
              </Field>
              <Field label="Requisito">
                <Input value={r.requirement} onChange={(e) => update(i, { requirement: e.target.value })} placeholder="Ex.: YPP" />
              </Field>
              <IconButton label="Tirar esta frente" icon={Trash2} onClick={() => setRows((rs) => rs.filter((_, j) => j !== i))} />
            </div>
          ))}
          <div className="flex flex-wrap gap-2">
            <Button size="sm" icon={Plus} onClick={() => setRows((rs) => [...rs, blank()])}>
              Adicionar frente
            </Button>
            <Button size="sm" type="submit" variant="primary" icon={Save} busy={save.isPending}>
              Salvar
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
              Cancelar
            </Button>
          </div>
          <ErrorBox error={save.error} title="Não foi possível salvar as frentes" />
        </form>
      ) : config.fronts.length ? (
        <>
          {config.plan ? <p className="text-sm text-muted">Plano: {config.plan}</p> : null}
          <ul className="flex flex-col gap-2">
            {config.fronts.map((f) => (
              <li key={f.id || f.name} className="flex flex-wrap items-center gap-2 text-sm">
                <Badge tone={TONE[f.status]}>{FRONT_STATUS_LABELS[f.status]}</Badge>
                <span className="font-medium text-fg">{f.name}</span>
                {f.next || f.requirement ? (
                  <span className="text-muted">→ {f.next || `precisa de ${f.requirement}`}</span>
                ) : null}
              </li>
            ))}
          </ul>
        </>
      ) : (
        <Empty title="Nenhuma frente ainda">Anote as formas de ganhar dinheiro do canal (afiliados, membros, patrocínio) e o próximo passo de cada uma.</Empty>
      )}
    </Section>
  );
};
