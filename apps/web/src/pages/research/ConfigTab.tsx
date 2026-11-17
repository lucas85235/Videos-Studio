/* Aba Configuração da pesquisa: concorrentes por link, palavras-chave por mercado, mercados, formatos, janela e orçamento. */
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ExternalLink, Plus, Save, Trash2, Undo2 } from 'lucide-react';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  RESEARCH_LIMITS,
  RESEARCH_MARKET_LABELS,
  researchApi,
  type ResearchCompetitor,
  type ResearchConfig,
  type ResearchFormat,
  type ResearchMarket,
  type ResearchState,
} from '../../apiResearch';
import { Button, Checkbox, ErrorBox, Field, FieldGroup, IconButton, Input, SegmentedControl, Textarea, useToast } from '../../ui';
import { formatLabel } from './shared';

type FormatChoice = 'auto' | 'long' | 'short' | 'both';

type Draft = {
  competitors: ResearchCompetitor[];
  keywords: string;
  keywords_us: string;
  markets: ResearchMarket[];
  formats: FormatChoice;
  budget: string;
  days: string;
};

const toDraft = (c: ResearchConfig): Draft => ({
  competitors: c.competitors,
  keywords: c.keywords_default ? '' : c.keywords.join('\n'),
  keywords_us: c.keywords_us.join('\n'),
  markets: c.markets,
  formats: !c.formats.length ? 'auto' : c.formats.length === 2 ? 'both' : c.formats[0],
  budget: String(c.budget),
  days: String(c.days),
});

const terms = (text: string) =>
  text
    .split(/\n|,/)
    .map((t) => t.trim().replace(/\s+/g, ' '))
    .filter(Boolean);

const termsError = (list: string[]) =>
  list.some((t) => t.includes('|'))
    ? 'Um termo por linha, sem "|" (cada busca é um termo só).'
    : list.length > RESEARCH_LIMITS.keywords
      ? `No máximo ${RESEARCH_LIMITS.keywords} termos (cada um é uma busca por mercado, do limite de 100 buscas por dia).`
      : undefined;

const intError = (text: string, [lo, hi]: readonly [number, number]) => {
  const n = Number(text);
  return Number.isInteger(n) && n >= lo && n <= hi ? undefined : `De ${lo} a ${hi}.`;
};

const FORMAT_VALUE: Record<FormatChoice, ResearchFormat[]> = { auto: [], long: ['long'], short: ['short'], both: ['long', 'short'] };

export const ConfigTab: React.FC<{ channelId: string; state: ResearchState }> = ({ channelId, state }) => {
  const qc = useQueryClient();
  const { toast } = useToast();
  const cfg = state.config;
  const [draft, setDraft] = useState<Draft>(() => toDraft(cfg));
  const [link, setLink] = useState('');
  // A config salva muda por fora (fim de uma rodada, "Rodar toda semana", outra aba): o rascunho só acompanha se não
  // tem alteração por salvar. "Rodar toda semana" e o estado das rodadas nem fazem parte do rascunho.
  const savedKey = JSON.stringify(toDraft(cfg));
  const lastSaved = useRef(savedKey);
  useEffect(() => {
    const prev = lastSaved.current;
    if (savedKey === prev) return;
    lastSaved.current = savedKey;
    setDraft((d) => (JSON.stringify(d) === prev ? (JSON.parse(savedKey) as Draft) : d));
  }, [savedKey]);
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setDraft((d) => ({ ...d, [k]: v }));

  const kw = terms(draft.keywords);
  const kwUs = terms(draft.keywords_us);
  const errors = {
    keywords: termsError(kw),
    keywords_us: termsError(kwUs),
    markets: draft.markets.length ? undefined : 'Escolha pelo menos um mercado.',
    budget: intError(draft.budget, RESEARCH_LIMITS.budget),
    days: intError(draft.days, RESEARCH_LIMITS.days),
  };
  const invalid = Object.values(errors).some(Boolean);
  const dirty = useMemo(() => JSON.stringify(draft) !== savedKey, [draft, savedKey]);

  const resolve = useMutation({
    mutationFn: () => researchApi.resolveCompetitor(link.trim()),
    onSuccess: (c) => {
      setLink('');
      if (draft.competitors.some((x) => x.id === c.id)) {
        toast({ tone: 'info', title: 'Esse concorrente já está na lista', description: c.name });
        return;
      }
      set('competitors', [...draft.competitors, { id: c.id, name: c.name, handle: c.handle }]);
    },
  });
  const save = useMutation({
    mutationFn: () =>
      researchApi.saveConfig(channelId, {
        competitors: draft.competitors,
        keywords: kw,
        keywords_us: kwUs,
        markets: draft.markets,
        formats: FORMAT_VALUE[draft.formats],
        budget: Number(draft.budget),
        days: Number(draft.days),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['research', 'state', channelId] });
      toast({ tone: 'good', title: 'Configuração da pesquisa salva' });
    },
  });
  const toggleMarket = (m: ResearchMarket, on: boolean) =>
    set('markets', (['br', 'us'] as const).filter((x) => (x === m ? on : draft.markets.includes(x))));
  const channelKeywords = cfg.keywords_default ? cfg.keywords : [];
  const searches = kw.length || channelKeywords.length;
  const usSearches = draft.markets.includes('us') ? kwUs.length || searches : 0;
  const brSearches = draft.markets.includes('br') ? searches : 0;
  const nCompetitors = draft.competitors.length;
  const nSearches = brSearches + usSearches;
  // cota geral: channels + playlistItems + videos dos concorrentes e 2 leituras por busca; a busca em si é do balde de 100 por dia
  const estimate = Math.ceil(nCompetitors / 50) + 2 * nCompetitors + 2 * nSearches;

  return (
    <form
      className="flex flex-col gap-5"
      onSubmit={(e) => {
        e.preventDefault();
        if (!invalid) save.mutate();
      }}
    >
      <FieldGroup legend="Concorrentes" hint="Cole o link do canal (@nome, /channel/UC…). O Vulto lê a página pública, sem gastar cota.">
        <div className="flex flex-wrap items-end gap-2">
          <Field label="Link do canal concorrente" className="min-w-[240px] flex-1">
            <Input
              value={link}
              onChange={(e) => setLink(e.target.value)}
              placeholder="https://www.youtube.com/@canal"
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  if (link.trim()) resolve.mutate();
                }
              }}
            />
          </Field>
          <Button
            icon={Plus}
            disabled={!link.trim() || draft.competitors.length >= RESEARCH_LIMITS.competitors}
            busy={resolve.isPending}
            onClick={() => resolve.mutate()}
          >
            Adicionar
          </Button>
        </div>
        <ErrorBox error={resolve.error} title="Não deu para achar o canal" onClose={() => resolve.reset()} />
        {draft.competitors.length ? (
          <ul className="flex flex-col divide-y divide-line rounded-lg border border-line">
            {draft.competitors.map((c) => (
              <li key={c.id} className="flex items-center gap-3 px-3 py-2 text-sm">
                <a
                  href={`https://www.youtube.com/channel/${c.id}`}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex min-w-0 flex-1 items-center gap-1 text-fg hover:text-accent"
                >
                  <span className="truncate">{c.name || c.id}</span>
                  {c.handle ? <span className="shrink-0 text-xs text-muted">{c.handle}</span> : null}
                  <ExternalLink size={12} aria-hidden="true" className="shrink-0" />
                </a>
                <IconButton
                  label={`Tirar ${c.name || c.id}`}
                  icon={Trash2}
                  size="sm"
                  onClick={() =>
                    set(
                      'competitors',
                      draft.competitors.filter((x) => x.id !== c.id),
                    )
                  }
                />
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-[13px] text-muted">Nenhum concorrente ainda. Com eles, a pesquisa acha os vídeos que foram muito acima da média de cada um.</p>
        )}
      </FieldGroup>

      <FieldGroup legend="Mercados" hint='Onde as buscas rodam. "EUA" acha ângulos de fora que ainda não existem em português.'>
        <div className="flex flex-wrap gap-x-6 gap-y-2">
          {(['br', 'us'] as const).map((m) => (
            <Checkbox key={m} label={RESEARCH_MARKET_LABELS[m]} checked={draft.markets.includes(m)} onChange={(on) => toggleMarket(m, on)} />
          ))}
        </div>
        {errors.markets ? <p className="text-[13px] text-red-300">{errors.markets}</p> : null}
      </FieldGroup>

      <div className="grid gap-4 md:grid-cols-2">
        <Field
          label="Palavras-chave (português)"
          hint={
            channelKeywords.length && !kw.length
              ? `Vazio: usa as palavras-chave do canal no YouTube (${channelKeywords.join(', ')}).`
              : `Um termo por linha, até ${RESEARCH_LIMITS.keywords}. Cada termo é uma busca por mercado (o YouTube libera 100 buscas por dia, somando o Radar).`
          }
          error={errors.keywords}
        >
          <Textarea rows={5} value={draft.keywords} onChange={(e) => set('keywords', e.target.value)} placeholder={channelKeywords.join('\n')} />
        </Field>
        {draft.markets.includes('us') ? (
          <Field
            label="Palavras-chave em inglês (EUA)"
            hint="Vazio: usa os mesmos termos em português (o que costuma achar pouco). Um termo por linha."
            error={errors.keywords_us}
          >
            <Textarea rows={5} value={draft.keywords_us} onChange={(e) => set('keywords_us', e.target.value)} />
          </Field>
        ) : null}
      </div>

      <FieldGroup legend="Formatos" hint={`A síntese só propõe estes formatos. Automático = os estilos do canal (hoje: ${state.formats.map(formatLabel).join(' + ')}).`}>
        <SegmentedControl
          value={draft.formats}
          onChange={(v) => set('formats', v)}
          options={[
            { id: 'auto', label: 'Automático' },
            { id: 'long', label: 'Só longos' },
            { id: 'short', label: 'Só Shorts' },
            { id: 'both', label: 'Longos e Shorts' },
          ]}
        />
      </FieldGroup>

      <div className="flex flex-wrap gap-4">
        <Field label="Janela (dias)" hint="Idade máxima dos vídeos olhados." error={errors.days} className="w-44">
          <Input type="number" inputMode="numeric" min={RESEARCH_LIMITS.days[0]} max={RESEARCH_LIMITS.days[1]} value={draft.days} onChange={(e) => set('days', e.target.value)} />
        </Field>
        <Field
          label="Orçamento da cota geral"
          hint={`Teto de unidades por rodada (as buscas contam à parte). Esta config: cerca de ${estimate.toLocaleString('pt-BR')} unidades e ${nSearches} ${nSearches === 1 ? 'busca' : 'buscas'}.`}
          error={errors.budget}
          className="w-64"
        >
          <Input
            type="number"
            inputMode="numeric"
            step={100}
            min={RESEARCH_LIMITS.budget[0]}
            max={RESEARCH_LIMITS.budget[1]}
            value={draft.budget}
            onChange={(e) => set('budget', e.target.value)}
          />
        </Field>
      </div>

      <ErrorBox error={save.error} title="Não deu para salvar" onClose={() => save.reset()} />
      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" variant="primary" icon={Save} disabled={!dirty || invalid} busy={save.isPending}>
          Salvar configuração
        </Button>
        <Button variant="ghost" icon={Undo2} disabled={!dirty} onClick={() => setDraft(JSON.parse(savedKey) as Draft)}>
          Desfazer
        </Button>
        {dirty ? <span className="text-xs text-amber-200">Alterações não salvas</span> : null}
      </div>
    </form>
  );
};
