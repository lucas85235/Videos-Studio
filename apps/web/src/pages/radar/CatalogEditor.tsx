/* Editor do catálogo do radar: nichos (termos EN/PT, duração, categoria), números da coleta, evitar e alertas. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Plus, RotateCcw, Save, Trash2 } from 'lucide-react';
import React, { useEffect, useState } from 'react';
import { radarApi, type RadarCatalog, type RadarCatalogNiche, type RadarPair } from '../../apiRadar';
import { Button, ErrorBox, Field, IconButton, Input, Select, Skeleton, Textarea, useConfirm, useToast } from '../../ui';

type Draft = Omit<RadarCatalogNiche, 'en' | 'pt'> & { en: string; pt: string };
const NUMBERS: { k: keyof Omit<RadarCatalog['radar'], 'evitar' | 'alertas'>; label: string; hint: string }[] = [
  { k: 'orcamento', label: 'Orçamento de cota', hint: 'Unidades por coleta (busca = 100, o resto = 1).' },
  { k: 'dias', label: 'Dias', hint: 'Vídeos publicados nos últimos N dias.' },
  { k: 'hit_us', label: 'Hit nos EUA', hint: 'Views para contar como hit.' },
  { k: 'hit_br', label: 'Hit no Brasil', hint: 'Views para contar como hit.' },
  { k: 'novo_meses', label: 'Canal novo (meses)', hint: 'Criado há até N meses.' },
  { k: 'candidatos', label: 'Canais lidos a fundo', hint: 'Canais novos com hit (2 unidades cada).' },
];

const lines = (s: string) =>
  s
    .split('\n')
    .map((x) => x.trim())
    .filter(Boolean);
const pairsText = (p: RadarPair[]) => p.map(([a, b]) => `${a}: ${b}`).join('\n');
const parsePairs = (s: string): RadarPair[] =>
  lines(s).map((l) => {
    const i = l.indexOf(':');
    return i < 0 ? [l, ''] : [l.slice(0, i).trim(), l.slice(i + 1).trim()];
  });

const toDraft = (n: RadarCatalogNiche): Draft => ({ ...n, en: n.en.join('\n'), pt: n.pt.join('\n') });

export const CatalogEditor: React.FC<{ onSaved?: () => void }> = ({ onSaved }) => {
  const qc = useQueryClient();
  const { toast } = useToast();
  const confirm = useConfirm();
  const catalog = useQuery({ queryKey: ['radar', 'catalog'], queryFn: radarApi.catalog });
  const [niches, setNiches] = useState<Draft[]>([]);
  const [numbers, setNumbers] = useState<Record<string, string>>({});
  const [avoid, setAvoid] = useState('');
  const [alerts, setAlerts] = useState('');

  useEffect(() => {
    const c = catalog.data;
    if (!c) return;
    setNiches(c.nichos.map(toDraft));
    setNumbers(Object.fromEntries(NUMBERS.map(({ k }) => [k, String(c.radar[k])])));
    setAvoid(pairsText(c.radar.evitar));
    setAlerts(pairsText(c.radar.alertas));
  }, [catalog.data]);

  const done = (c: RadarCatalog, title: string) => {
    qc.setQueryData(['radar', 'catalog'], c);
    qc.invalidateQueries({ queryKey: ['radar'] });
    toast({ tone: 'good', title });
    onSaved?.();
  };
  const save = useMutation({
    mutationFn: () => {
      const c = catalog.data!;
      const radar = { ...c.radar, evitar: parsePairs(avoid), alertas: parsePairs(alerts) };
      for (const { k } of NUMBERS) radar[k] = Number(numbers[k]);
      return radarApi.saveCatalog({ radar, rpm: c.rpm, nichos: niches.map((n) => ({ ...n, en: lines(n.en), pt: lines(n.pt) })) });
    },
    onSuccess: (c) => done(c, 'Catálogo salvo'),
  });
  const reset = useMutation({ mutationFn: radarApi.resetCatalog, onSuccess: (c) => done(c, 'Catálogo de volta ao padrão') });

  if (catalog.isLoading) return <Skeleton className="h-40" />;
  if (catalog.error || !catalog.data) return <ErrorBox error={catalog.error} onRetry={() => catalog.refetch()} />;
  const cats = Object.keys(catalog.data.rpm);
  const patch = (i: number, p: Partial<Draft>) => setNiches((list) => list.map((n, j) => (j === i ? { ...n, ...p } : n)));

  return (
    <div className="flex flex-col gap-4">
      <p className="text-xs text-muted">
        Um termo por linha: cada termo é uma busca (100 unidades). O orçamento cobre o 1º termo de todos os nichos e os seguintes enquanto der. Não use |
        (OR): a API devolve vídeos minúsculos. {catalog.data.custom ? 'Este catálogo foi editado.' : 'Este é o catálogo padrão do Vulto.'}
      </p>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {NUMBERS.map(({ k, label, hint }) => (
          <Field key={k} label={label} hint={hint}>
            <Input inputMode="numeric" value={numbers[k] ?? ''} onChange={(e) => setNumbers((n) => ({ ...n, [k]: e.target.value }))} />
          </Field>
        ))}
      </div>
      <ul className="flex flex-col gap-3">
        {niches.map((n, i) => (
          <li key={i} className="grid gap-2 rounded-lg border border-line p-3 md:grid-cols-[repeat(4,minmax(0,1fr))_auto]">
            <Field label="Nome">
              <Input value={n.nome} onChange={(e) => patch(i, { nome: e.target.value })} />
            </Field>
            <Field label="Id" hint="Letras minúsculas, números e hífen.">
              <Input value={n.id} onChange={(e) => patch(i, { id: e.target.value })} />
            </Field>
            <Field label="Categoria (RPM)">
              <Select value={n.cat} onChange={(e) => patch(i, { cat: e.target.value })}>
                {(cats.includes(n.cat) || !n.cat ? cats : [n.cat, ...cats]).map((c) => (
                  <option key={c}>{c}</option>
                ))}
              </Select>
            </Field>
            <Field label="Duração">
              <Select value={n.duracao} onChange={(e) => patch(i, { duracao: e.target.value as Draft['duracao'] })}>
                <option value="medium">4 a 20 min</option>
                <option value="long">Mais de 20 min</option>
              </Select>
            </Field>
            <IconButton className="self-end" label="Tirar o nicho" icon={Trash2} variant="ghost" onClick={() => setNiches((l) => l.filter((_, j) => j !== i))} />
            <Field label="Termos em inglês (EUA)" className="md:col-span-2">
              <Textarea rows={2} value={n.en} onChange={(e) => patch(i, { en: e.target.value })} />
            </Field>
            <Field label="Termos em português (Brasil)" className="md:col-span-2">
              <Textarea rows={2} value={n.pt} onChange={(e) => patch(i, { pt: e.target.value })} />
            </Field>
          </li>
        ))}
      </ul>
      <Button
        className="self-start"
        icon={Plus}
        onClick={() => setNiches((l) => [...l, { id: '', nome: '', cat: cats[0] ?? '', duracao: 'medium', en: '', pt: '' }])}
      >
        Novo nicho
      </Button>
      <div className="grid gap-3 md:grid-cols-2">
        <Field label="Evitar no primeiro canal" hint="Uma linha por item: “formato: por quê”. Aparece no painel.">
          <Textarea rows={5} value={avoid} onChange={(e) => setAvoid(e.target.value)} />
        </Field>
        <Field label="Alertas" hint="Uma linha por item: “palavra: aviso”. Canal com a palavra no nome ou em metade dos títulos fica marcado e fora das contas.">
          <Textarea rows={5} value={alerts} onChange={(e) => setAlerts(e.target.value)} />
        </Field>
      </div>
      <ErrorBox error={save.error || reset.error} title="Não deu para salvar o catálogo" onClose={() => (save.reset(), reset.reset())} />
      <div className="flex flex-wrap gap-2">
        <Button variant="primary" icon={Save} busy={save.isPending} onClick={() => save.mutate()}>
          Salvar catálogo
        </Button>
        <Button
          icon={RotateCcw}
          busy={reset.isPending}
          onClick={async () => {
            if (await confirm({ title: 'Voltar ao catálogo padrão?', description: 'As mudanças do catálogo se perdem.', confirmLabel: 'Voltar ao padrão' }))
              reset.mutate();
          }}
        >
          Voltar ao padrão
        </Button>
      </div>
    </div>
  );
};
