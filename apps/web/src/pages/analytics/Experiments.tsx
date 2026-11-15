import { useMutation, useQuery } from '@tanstack/react-query';
import { FlaskConical, Plus, X } from 'lucide-react';
import React, { useState } from 'react';
import {
  analyticsApi,
  type Experiment,
  type ExperimentMetric,
  METRIC_LABELS,
} from '../../apiAnalytics';
import { Badge, Button, Checkbox, Empty, ErrorBox, Field, Input, Select, Textarea, useToast } from '../../ui';
import { fmtCount } from './format';

/** Valores conhecidos de cada variável (para sugerir variantes). Hora, dia e faixas têm os mesmos cortes do backend. */
const KNOWN_VALUES: Record<string, string[]> = {
  formato: ['short', 'long'],
  hora: ['madrugada (0-6h)', 'manhã (6-12h)', 'tarde (12-18h)', 'noite (18-24h)'],
  dia: ['dia útil', 'fim de semana'],
  faixa_duracao: ['até 35 s', '35 a 45 s', 'mais de 45 s'],
  faixa_nota: ['nota abaixo de 7,5', 'nota 7,5 a 8', 'nota 8+'],
  origem: ['original', 'corte do longo'],
};

const fmtMedian = (metric: string, v: number | null) => {
  if (v === null) return '—';
  if (metric === 'ctr' || metric === 'retencao') return `${v.toFixed(1).replace('.', ',')}%`;
  if (metric === 'avd_s') return `${v.toFixed(1).replace('.', ',')} s`;
  return fmtCount(v);
};

/* ------------------------------------------------------------------ */
/* Criar experimento                                                   */
/* ------------------------------------------------------------------ */

export const NewExperiment: React.FC<{ channelId: string | null; onDone: () => void; onCancel: () => void }> = ({ channelId, onDone, onCancel }) => {
  const { toast } = useToast();
  const vars = useQuery({ queryKey: ['analytics-variables'], queryFn: analyticsApi.variables, staleTime: Infinity });
  const [variable, setVariable] = useState('hora');
  const [variants, setVariants] = useState<string[]>([]);
  const [custom, setCustom] = useState('');
  const [metric, setMetric] = useState<ExperimentMetric>('views');
  const [hypothesis, setHypothesis] = useState('');
  const create = useMutation({
    mutationFn: () => analyticsApi.createExperiment({ channel_id: channelId, hypothesis: hypothesis.trim(), variable, variants, metric }),
    onSuccess: () => {
      toast({ tone: 'good', title: 'Experimento aberto' });
      onDone();
    },
  });
  const known = KNOWN_VALUES[variable] ?? [];
  const toggle = (v: string, on: boolean) => setVariants((cur) => (on ? [...cur, v] : cur.filter((x) => x !== v)));
  const addCustom = () => {
    const v = custom.trim();
    if (v && !variants.includes(v)) setVariants([...variants, v]);
    setCustom('');
  };
  const valid = hypothesis.trim().length >= 3 && variants.length >= 2;
  return (
    <form
      className="flex flex-col gap-3 rounded-lg border border-line p-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (valid) create.mutate();
      }}
    >
      <Field label="Hipótese" hint="O que você acha que vai acontecer. Ex.: Shorts de manhã têm mais views.">
        <Input value={hypothesis} onChange={(e) => setHypothesis(e.target.value)} maxLength={2000} />
      </Field>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Variável">
          <Select
            value={variable}
            onChange={(e) => {
              setVariable(e.target.value);
              setVariants([]);
            }}
          >
            {(vars.data ?? [{ id: 'hora', label: 'Hora da publicação' }]).map((v) => (
              <option key={v.id} value={v.id}>
                {v.label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Métrica">
          <Select value={metric} onChange={(e) => setMetric(e.target.value as ExperimentMetric)}>
            {(Object.keys(METRIC_LABELS) as ExperimentMetric[]).map((m) => (
              <option key={m} value={m}>
                {METRIC_LABELS[m]}
              </option>
            ))}
          </Select>
        </Field>
      </div>
      <div className="flex flex-col gap-1.5">
        <div className="text-sm font-medium text-fg">Variantes (2 ou mais)</div>
        {known.length ? (
          <div className="flex flex-wrap gap-x-4 gap-y-1.5">
            {known.map((v) => (
              <Checkbox key={v} label={v} checked={variants.includes(v)} onChange={(on) => toggle(v, on)} />
            ))}
          </div>
        ) : null}
        <div className="flex flex-wrap items-center gap-2">
          <Input
            aria-label="Outra variante"
            placeholder={known.length ? 'Outra variante' : 'Valor da variante (ex.: lista)'}
            value={custom}
            onChange={(e) => setCustom(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                addCustom();
              }
            }}
            className="max-w-xs"
          />
          <Button size="sm" icon={Plus} onClick={addCustom} disabled={!custom.trim()}>
            Adicionar
          </Button>
        </div>
        {variants.filter((v) => !known.includes(v)).length ? (
          <div className="flex flex-wrap gap-1.5">
            {variants
              .filter((v) => !known.includes(v))
              .map((v) => (
                <button key={v} type="button" onClick={() => toggle(v, false)} className="inline-flex items-center gap-1" aria-label={`Tirar ${v}`}>
                  <Badge>
                    {v} <X size={12} aria-hidden="true" />
                  </Badge>
                </button>
              ))}
          </div>
        ) : null}
      </div>
      <ErrorBox error={create.error} title="Não deu para abrir o experimento" />
      <div className="flex flex-wrap justify-end gap-2">
        <Button variant="ghost" onClick={onCancel}>
          Cancelar
        </Button>
        <Button type="submit" variant="primary" disabled={!valid} busy={create.isPending}>
          Abrir experimento
        </Button>
      </div>
    </form>
  );
};

/* ------------------------------------------------------------------ */
/* Fechar experimento (com lição opcional)                             */
/* ------------------------------------------------------------------ */

const CloseForm: React.FC<{ exp: Experiment; onDone: () => void; onCancel: () => void }> = ({ exp, onDone, onCancel }) => {
  const { toast } = useToast();
  const [result, setResult] = useState(exp.evaluation?.text ?? '');
  const [learning, setLearning] = useState('');
  const [abandon, setAbandon] = useState(false);
  const [saveLesson, setSaveLesson] = useState(true);
  const close = useMutation({
    // Fechar e registrar a lição num pedido só: se a lição falhar, o experimento continua aberto e dá para tentar de novo.
    mutationFn: () =>
      analyticsApi.closeExperiment(exp.id, { result, learning, abandon, save_lesson: saveLesson && learning.trim().length >= 3 }),
    onSuccess: () => {
      toast({ tone: 'good', title: abandon ? 'Experimento abandonado' : 'Experimento concluído' });
      onDone();
    },
  });
  return (
    <form
      className="flex flex-col gap-3 border-t border-line pt-3"
      onSubmit={(e) => {
        e.preventDefault();
        close.mutate();
      }}
    >
      <Field label="Resultado">
        <Input value={result} onChange={(e) => setResult(e.target.value)} maxLength={4000} />
      </Field>
      <Field label="O que aprendeu" hint="Vira uma lição proposta, para aplicar depois.">
        <Textarea rows={2} value={learning} onChange={(e) => setLearning(e.target.value)} maxLength={4000} />
      </Field>
      <div className="flex flex-wrap gap-x-5 gap-y-2">
        <Checkbox label="Registrar como lição" checked={saveLesson} onChange={setSaveLesson} />
        <Checkbox label="Abandonar (sem conclusão)" checked={abandon} onChange={setAbandon} />
      </div>
      <ErrorBox error={close.error} title="Não deu para fechar o experimento" />
      <div className="flex flex-wrap justify-end gap-2">
        <Button variant="ghost" onClick={onCancel}>
          Cancelar
        </Button>
        <Button type="submit" variant="primary" busy={close.isPending}>
          Fechar experimento
        </Button>
      </div>
    </form>
  );
};

/* ------------------------------------------------------------------ */
/* Lista de experimentos abertos                                       */
/* ------------------------------------------------------------------ */

const ExperimentCard: React.FC<{ exp: Experiment; label: string; onChanged: () => void }> = ({ exp, label, onChanged }) => {
  const [closing, setClosing] = useState(false);
  const ev = exp.evaluation;
  return (
    <li className="flex flex-col gap-2 rounded-lg border border-line p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="font-medium text-fg">{exp.hypothesis}</div>
          <div className="mt-0.5 text-[13px] text-subtle">
            {label} · {METRIC_LABELS[exp.metric] ?? exp.metric}
            {exp.project_ids.length ? ` · ${exp.project_ids.length} projetos` : ''}
          </div>
        </div>
        {!closing ? (
          <Button size="sm" onClick={() => setClosing(true)}>
            Fechar
          </Button>
        ) : null}
      </div>
      {ev ? (
        <>
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <Badge tone={ev.winner ? 'good' : ev.text.startsWith('cedo') ? 'neutral' : 'info'}>{ev.winner ? 'Tem vencedor' : ev.text.startsWith('cedo') ? 'Cedo' : 'Empate'}</Badge>
            <span className="text-fg">{ev.text}</span>
          </div>
          <ul className="flex flex-wrap gap-x-4 gap-y-1 text-[13px] text-muted">
            {ev.variants.map((v) => (
              <li key={v.variant}>
                {v.variant}: <span className="tabular-nums text-fg">{fmtMedian(exp.metric, v.median)}</span> (n={v.n})
              </li>
            ))}
          </ul>
          {ev.young ? <p className="text-[13px] text-subtle">{ev.young} vídeo(s) ainda novos demais para contar.</p> : null}
        </>
      ) : null}
      {closing ? (
        <CloseForm
          exp={exp}
          onCancel={() => setClosing(false)}
          onDone={() => {
            setClosing(false);
            onChanged();
          }}
        />
      ) : null}
    </li>
  );
};

export const OpenExperiments: React.FC<{
  experiments: Experiment[];
  labels: Record<string, string>;
  error?: unknown;
  onChanged: () => void;
}> = ({ experiments, labels, error, onChanged }) => {
  if (error) return <ErrorBox error={error} />;
  if (!experiments.length) {
    return (
      <Empty icon={FlaskConical} title="Nenhum experimento aberto">
        Abra um para testar uma ideia (hora, duração, tipo de Short...). A leitura sai quando cada variante tiver 3 ou mais vídeos na idade mínima.
      </Empty>
    );
  }
  return (
    <ul className="flex flex-col gap-3">
      {experiments.map((e) => (
        <ExperimentCard key={e.id} exp={e} label={labels[e.variable] ?? e.variable} onChanged={onChanged} />
      ))}
    </ul>
  );
};
