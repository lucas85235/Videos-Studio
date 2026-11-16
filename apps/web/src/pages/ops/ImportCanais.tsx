// Configurações › Importar do canais: prévia do que entra e importação do histórico do projeto antigo.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { DatabaseZap, Eye, Info } from 'lucide-react';
import React, { useEffect, useRef, useState } from 'react';
import { api, type Channel } from '../../api';
import { canaisDefaults, type CanaisChoice, type CanaisReport, importCanais, previewCanais } from '../../apiImport';
import { Badge, type BadgeTone, Button, ErrorBox, Field, Input, Section, Select, useConfirm, useToast } from '../../ui';

// Rótulo da ação no canal: [prévia, depois de importar].
const ACTION_LABEL: Record<string, { text: [string, string]; tone: BadgeTone }> = {
  criar: { text: ['Cria canal novo', 'Canal criado'], tone: 'accent' },
  associar: { text: ['Usa canal existente', 'Canal existente'], tone: 'info' },
  pular: { text: ['Fica de fora', 'Ficou de fora'], tone: 'neutral' },
};

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

const Report: React.FC<{
  report: CanaisReport;
  channels: Channel[];
  choices: Record<string, CanaisChoice>;
  onChoice?: (slug: string, value: CanaisChoice) => void;
  busy?: boolean;
}> = ({ report, channels, choices, onChoice, busy }) => (
  <div className="flex flex-col gap-4">
    <div>
      <h3 className="mb-2 text-[13px] font-medium text-muted">Canais</h3>
      <ul className="flex flex-col gap-2">
        {report.channels.map((c) => {
          const action = ACTION_LABEL[c.action] ?? ACTION_LABEL.pular;
          return (
            <li key={c.slug} className="rounded-lg border border-line bg-canvas p-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium text-fg">{c.name}</span>
                <span className="font-mono text-xs text-subtle">{c.slug}</span>
                <Badge tone={action.tone}>{action.text[report.dry_run ? 0 : 1]}</Badge>
                {c.vulto_name && (c.action === 'associar' || !report.dry_run) ? <span className="text-[13px] text-muted">→ {c.vulto_name}</span> : null}
                <span className="text-[13px] text-subtle">({c.reason})</span>
              </div>
              {c.fields.length ? <p className="mt-1 text-[13px] text-muted">Preenche: {c.fields.join(', ')}.</p> : null}
              {c.offers_new || c.offers_existing ? (
                <p className="mt-0.5 text-[13px] text-muted">
                  Ofertas de afiliado: {c.offers_new} novas{c.offers_existing ? `, ${c.offers_existing} já existiam` : ''}.
                </p>
              ) : null}
              {onChoice ? (
                <Field label="Destino no Vulto" className="mt-2 max-w-sm">
                  <Select value={choices[c.slug] ?? 'auto'} disabled={busy} onChange={(e) => onChoice(c.slug, e.target.value)}>
                    <option value="auto">Automático (mesmo canal ou cria um novo)</option>
                    <option value="pular">Não importar este canal</option>
                    {channels.map((ch) => {
                      // Dois canais do canais no mesmo canal do Vulto misturariam as linhas: o já escolhido fica bloqueado.
                      const taken = Object.entries(choices).some(([slug, v]) => slug !== c.slug && v === ch.id);
                      return (
                        <option key={ch.id} value={ch.id} disabled={taken}>
                          Usar “{ch.name}”{taken ? ' (já escolhido para outro canal)' : ''}
                        </option>
                      );
                    })}
                  </Select>
                </Field>
              ) : null}
            </li>
          );
        })}
      </ul>
    </div>
    <div>
      <h3 className="mb-2 text-[13px] font-medium text-muted">Histórico</h3>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-[13px] text-muted">
              <th className="py-1 pr-3 font-medium">Tabela</th>
              <th className="py-1 pr-3 text-right font-medium">No canais</th>
              <th className="py-1 pr-3 text-right font-medium">{report.dry_run ? 'Entrariam' : 'Entraram'}</th>
              <th className="py-1 pr-3 text-right font-medium">Já existiam</th>
              <th className="py-1 text-right font-medium">Fora</th>
            </tr>
          </thead>
          <tbody>
            {report.tables.map((t) => (
              <tr key={t.key} className="border-t border-line align-top">
                <td className="py-1.5 pr-3 text-fg">
                  {t.label}
                  {t.examples.length ? (
                    <div className="mt-0.5 max-w-md truncate text-xs text-subtle" title={t.examples.join(' · ')}>
                      ex.: {t.examples.join(' · ')}
                    </div>
                  ) : null}
                </td>
                <td className="py-1.5 pr-3 text-right tabular-nums text-muted">{t.total}</td>
                <td className="py-1.5 pr-3 text-right tabular-nums text-fg">{t.new}</td>
                <td className="py-1.5 pr-3 text-right tabular-nums text-muted">
                  {t.existing}
                  {t.updated ? <div className="text-xs text-subtle">{plural(t.updated, 'completada', 'completadas')}</div> : null}
                </td>
                <td className="py-1.5 text-right tabular-nums text-muted">{t.skipped}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
    {report.notes.length ? (
      <ul className="flex flex-col gap-1 text-[13px] text-muted">
        {report.notes.map((n) => (
          <li key={n} className="flex gap-1.5">
            <Info size={14} aria-hidden="true" className="mt-0.5 shrink-0 text-subtle" />
            <span>{n}</span>
          </li>
        ))}
      </ul>
    ) : null}
  </div>
);

export const ImportCanaisSection: React.FC = () => {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const { toast } = useToast();
  const defaults = useQuery({ queryKey: ['import-canais-defaults'], queryFn: canaisDefaults, staleTime: Infinity });
  const channels = useQuery({ queryKey: ['channels'], queryFn: () => api.get<Channel[]>('/api/channels') });
  const [root, setRoot] = useState('');
  const [choices, setChoices] = useState<Record<string, CanaisChoice>>({});
  const [done, setDone] = useState<CanaisReport | null>(null);
  // Última prévia (fica na tela enquanto a próxima é calculada) e as escolhas com que ela foi feita.
  const [plan, setPlan] = useState<CanaisReport | null>(null);
  const [planChoices, setPlanChoices] = useState<Record<string, CanaisChoice>>({});
  const filled = useRef(false);
  const latest = useRef(0);

  // A pasta padrão entra uma vez só: depois o campo pode ficar vazio enquanto a pessoa digita outro caminho.
  useEffect(() => {
    if (defaults.data && !filled.current) {
      filled.current = true;
      const fallback = defaults.data.default_root;
      setRoot((cur) => cur || fallback);
    }
  }, [defaults.data]);

  const preview = useMutation({
    mutationFn: async (next: Record<string, CanaisChoice>) => {
      const id = ++latest.current;
      return { id, next, report: await previewCanais({ root, channels: next }) };
    },
    // Só a resposta do último pedido vale: uma prévia lenta de antes não sobrescreve a mais nova.
    onSuccess: ({ id, next, report }) => {
      if (id !== latest.current) return;
      setPlan(report);
      setPlanChoices(next);
    },
  });
  // A importação só sai com a prévia feita com as escolhas atuais (se a última prévia falhou, não confere).
  const sameChoices = (a: Record<string, CanaisChoice>, b: Record<string, CanaisChoice>) => {
    const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
    return [...keys].every((k) => (a[k] ?? 'auto') === (b[k] ?? 'auto'));
  };
  const planReady = !!plan && sameChoices(choices, planChoices);
  const run = useMutation({
    mutationFn: () => importCanais({ root, channels: choices }),
    onSuccess: (report) => {
      setDone(report);
      setPlan(null);
      void qc.invalidateQueries();
      const total = report.tables.reduce((n, t) => n + t.new, 0);
      toast({ tone: 'good', title: `Importação feita: ${plural(total, 'registro novo', 'registros novos')}` });
    },
  });

  // Trocar a pasta invalida a prévia: a importação só sai com a prévia em dia.
  const stale = () => {
    latest.current += 1; // prévia ainda em andamento para a pasta antiga é ignorada
    preview.reset();
    setPlan(null);
    setPlanChoices({});
    setDone(null);
    setChoices({});
  };

  const onImport = async () => {
    if (!plan || !planReady) return;
    const total = plan.tables.reduce((n, t) => n + t.new, 0);
    const created = plan.channels.filter((c) => c.action === 'criar').length;
    const ok = await confirm({
      title: 'Importar o histórico do canais?',
      description: `Entram ${plural(total, 'registro novo', 'registros novos')}${
        created ? ` e ${plural(created, 'canal novo', 'canais novos')}` : ''
      }. A pasta do canais não é alterada e rodar de novo não duplica nada. Numa nova importação só as métricas são atualizadas: ideias, experimentos, lições e pendências que já entraram ficam como estão.`,
      confirmLabel: 'Importar',
    });
    if (ok) run.mutate();
  };

  return (
    <div id="importar-canais">
      <Section
        title="Importar do canais"
        icon={DatabaseZap}
        description="Traz ideias, métricas, experimentos, lições, pendências, receita e a configuração de cada canal (canal.toml) do projeto antigo. Só lê a pasta dele."
      >
        <div className="flex flex-wrap items-end gap-2">
          <Field label="Pasta do canais" className="min-w-[16rem] flex-1">
            <Input
              value={root}
              onChange={(e) => {
                setRoot(e.target.value);
                stale();
              }}
              placeholder="C:\Users\voce\canais"
              spellCheck={false}
            />
          </Field>
          <Button icon={Eye} onClick={() => preview.mutate(choices)} busy={preview.isPending} disabled={!root.trim() || preview.isPending}>
            Ver prévia
          </Button>
          <Button variant="primary" onClick={() => void onImport()} busy={run.isPending} disabled={!planReady || preview.isPending || run.isPending}>
            Importar
          </Button>
        </div>
        {defaults.data && !defaults.data.exists && root === defaults.data.default_root ? (
          <p className="text-[13px] text-muted">A pasta padrão não existe neste computador: informe onde está o canais.</p>
        ) : null}
        <ErrorBox error={preview.error} title="Não foi possível ler o canais" />
        <ErrorBox error={run.error} title="A importação falhou" />
        {plan ? (
          <Report
            report={plan}
            channels={channels.data ?? []}
            choices={choices}
            busy={preview.isPending}
            onChoice={(slug, value) => {
              // Nova prévia na hora, com o destino escolhido.
              const next = { ...choices, [slug]: value };
              setChoices(next);
              preview.mutate(next);
            }}
          />
        ) : null}
        {done ? <Report report={done} channels={channels.data ?? []} choices={choices} /> : null}
      </Section>
    </div>
  );
};
