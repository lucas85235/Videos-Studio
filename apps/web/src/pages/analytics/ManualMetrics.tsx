import { useMutation } from '@tanstack/react-query';
import React, { useState } from 'react';
import { analyticsApi, type ManualMetricInput } from '../../apiAnalytics';
import { Button, ErrorBox, Field, Input, useToast } from '../../ui';
import { parseCount, parseNumber, todayIso } from './format';

type NumKey = 'views' | 'likes' | 'comments' | 'impressions' | 'ctr' | 'pct_watched' | 'avg_view_s' | 'subscribers_gained' | 'age_hours';

const FIELDS: { key: NumKey; label: string; pct?: boolean; int?: boolean }[] = [
  { key: 'views', label: 'Views', int: true },
  { key: 'likes', label: 'Likes', int: true },
  { key: 'comments', label: 'Comentários', int: true },
  { key: 'impressions', label: 'Impressões', int: true },
  { key: 'ctr', label: 'CTR (%)', pct: true },
  { key: 'pct_watched', label: 'Retenção (%)', pct: true },
  { key: 'avg_view_s', label: 'Duração média vista (s)' },
  { key: 'subscribers_gained', label: 'Inscritos ganhos', int: true },
  // Opcional: sem ela, a idade sai da data de publicação que o Vulto conhece.
  { key: 'age_hours', label: 'Idade (h), se souber' },
];

/** Tira o id de um link do YouTube (watch?v=, youtu.be/, /shorts/) ou devolve o texto como veio. */
const videoIdOf = (text: string) => {
  const t = text.trim();
  const m = t.match(/(?:v=|youtu\.be\/|\/shorts\/)([\w-]{6,})/);
  return m ? m[1] : t;
};

/** Lançar métricas à mão (campo vazio não apaga o número que já estava). */
export const ManualMetrics: React.FC<{ channelId?: string | null; onSaved: () => void }> = ({ channelId, onSaved }) => {
  const { toast } = useToast();
  const [video, setVideo] = useState('');
  const [day, setDay] = useState(todayIso());
  const [values, setValues] = useState<Record<NumKey, string>>(() => Object.fromEntries(FIELDS.map((f) => [f.key, ''])) as Record<NumKey, string>);

  const errors: Partial<Record<NumKey, string>> = {};
  // O canal do filtro só vale para vídeo sem publicação no Vulto (a publicação manda).
  const body: ManualMetricInput = { youtube_id: videoIdOf(video), day: day || undefined, channel_id: channelId ?? null };
  for (const f of FIELDS) {
    // Contagens: "12.500" é doze mil e quinhentos (ponto de milhar), nunca 12,5.
    const n = f.int ? parseCount(values[f.key]) : parseNumber(values[f.key]);
    if (n === undefined) continue;
    if (f.int && Number.isNaN(n)) errors[f.key] = 'Use um número inteiro (pode ter ponto de milhar: 12.500).';
    else if (Number.isNaN(n) || n < 0) errors[f.key] = 'Use um número a partir de 0.';
    else if (f.pct && n > 100) errors[f.key] = 'Vai de 0 a 100.';
    else body[f.key] = n;
  }
  const filled = FIELDS.some((f) => f.key !== 'age_hours' && body[f.key] !== undefined);
  const valid = Boolean(body.youtube_id) && filled && !Object.keys(errors).length;

  const save = useMutation({
    mutationFn: () => analyticsApi.addMetric(body),
    onSuccess: (row) => {
      toast({ tone: 'good', title: 'Métricas lançadas', description: `${row.youtube_id} em ${row.day.split('-').reverse().join('/')}` });
      setValues(Object.fromEntries(FIELDS.map((f) => [f.key, ''])) as Record<NumKey, string>);
      onSaved();
    },
  });

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (valid) save.mutate();
      }}
    >
      <div className="grid gap-3 sm:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <Field label="Vídeo do YouTube" hint="Link ou id do vídeo.">
          <Input value={video} onChange={(e) => setVideo(e.target.value)} placeholder="https://youtube.com/shorts/…" />
        </Field>
        <Field label="Dia">
          <Input type="date" value={day} onChange={(e) => setDay(e.target.value)} />
        </Field>
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {FIELDS.map((f) => (
          <Field key={f.key} label={f.label} error={errors[f.key]}>
            <Input inputMode="decimal" value={values[f.key]} onChange={(e) => setValues({ ...values, [f.key]: e.target.value })} />
          </Field>
        ))}
      </div>
      <p className="text-[13px] text-subtle">Deixe vazio o que você não sabe: campo vazio não apaga um número já lançado.</p>
      <ErrorBox error={save.error} title="Não deu para lançar" />
      <div className="flex justify-end">
        <Button type="submit" variant="primary" disabled={!valid} busy={save.isPending}>
          Lançar métricas
        </Button>
      </div>
    </form>
  );
};
