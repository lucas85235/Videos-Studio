import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Palette, Pencil, Plus, RotateCw, Save, Search, Sparkles, TriangleAlert, X } from 'lucide-react';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { api, DEFAULT_STYLE_ID, type StyleItem, type StyleSpec } from '../api';
import { STRUCTURE_LABELS, label } from '../labels';
import { useProviders } from '../shell/data';
import { Badge, Button, Card, Empty, ErrorBox, Field, Input, Page, PageHeader, Section, Select, Skeleton, Textarea, useConfirm, useToast } from '../ui';
import {
  ColorSwatch,
  StyleFonts,
  StyleThumb,
  colorsIn,
  familyLabel,
  fieldLabel,
  formatDuration,
  formatLong,
  formatShort,
  formatValue,
  sectionOfPath,
  suggestStyleId,
} from './styles/shared';

type Meta = { families: { id: string; label: string; implemented: boolean }[] };

type FromText = {
  style: StyleSpec;
  summary: string;
  applied: { path: string; value: unknown; reason: string }[];
  rejected: { path: string; error: string }[];
  provider: string;
  base_style_id?: string;
};

/** Resultado guardado junto com a base usada na geração (trocar "Partir de" depois não muda a origem). */
type Generated = FromText & { base: string };

type FormatFilter = 'todos' | 'curto' | 'longo';
type OriginFilter = 'todos' | 'meus' | 'presets';

const FORMAT_FILTERS: { id: FormatFilter; label: string }[] = [
  { id: 'todos', label: 'Todos' },
  { id: 'curto', label: 'Curtos 9:16' },
  { id: 'longo', label: 'Longos 16:9' },
];

const ORIGIN_FILTERS: { id: OriginFilter; label: string }[] = [
  { id: 'todos', label: 'Todos' },
  { id: 'meus', label: 'Meus estilos' },
  { id: 'presets', label: 'Presets' },
];

const DEFAULT_BASE = DEFAULT_STYLE_ID;
const DESCRIPTION_ID = 'estilo-novo-descricao';

const normalize = (text: string) =>
  text
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase();

/* ------------------------------------------------------------------ */
/* Grupo de botões de filtro (aria-pressed)                            */
/* ------------------------------------------------------------------ */

function ChipGroup<T extends string>({
  label: groupLabel,
  options,
  value,
  onChange,
}: {
  label: string;
  options: { id: T; label: string }[];
  value: T;
  onChange: (id: T) => void;
}) {
  return (
    <div role="group" aria-label={groupLabel} className="flex w-fit flex-wrap gap-0.5 rounded-lg border border-line bg-canvas p-0.5">
      {options.map((o) => (
        <button
          key={o.id}
          type="button"
          aria-pressed={value === o.id}
          onClick={() => onChange(o.id)}
          className={`min-h-7 rounded-md px-2.5 py-1 text-[13px] font-medium transition-colors ${
            value === o.id ? 'bg-accent/15 text-accent ring-1 ring-inset ring-accent/50' : 'text-muted hover:text-fg'
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Cartão de estilo                                                    */
/* ------------------------------------------------------------------ */

const StyleCard: React.FC<{ item: StyleItem; metaLabel: (id: string) => string | undefined; parentName?: string | null }> = ({ item, metaLabel, parentName }) => {
  const s = item.style;
  // Mesmo critério do seletor de estilo do Novo projeto: a família principal; a mistura, quando houver, fica na dica.
  const mix = s.families_mix.filter((f) => f !== s.family);
  const familyTitle = mix.length ? `Mistura com ${mix.map((f) => familyLabel(f, metaLabel(f))).join(', ')}` : undefined;
  const navigate = useNavigate();
  const to = `/estilos/${s.id}`;
  // O título é o link (teclado, leitor de tela, abrir em nova aba); o clique no resto do cartão também abre o editor.
  // Assim o "Renomear" é outro link, sem um <a> dentro de outro, e as dicas dos selos continuam aparecendo.
  const openFromCard = (e: React.MouseEvent<HTMLDivElement>) => {
    if (e.defaultPrevented || (e.target as HTMLElement).closest('a, button')) return;
    if (window.getSelection()?.toString()) return; // estava selecionando texto
    navigate(to);
  };
  return (
    <Card interactive onClick={openFromCard} className="flex h-full cursor-pointer gap-3">
      <StyleThumb style={s} />
      <div className="flex min-w-0 flex-1 flex-col gap-1.5">
        <div className="flex items-start justify-between gap-2">
          <Link
            to={to}
            title={s.name}
            className="line-clamp-2 min-w-0 rounded-sm font-display text-[15px] font-semibold leading-snug text-fg [overflow-wrap:anywhere]"
          >
            {s.name}
          </Link>
          {item.source === 'preset' ? null : (
            <Badge tone="info" className="shrink-0" title={`Versão ${item.version}`}>
              Meu · v{item.version}
            </Badge>
          )}
        </div>
        {s.description ? <p className="line-clamp-2 text-[13px] leading-snug text-muted [overflow-wrap:anywhere]">{s.description}</p> : null}
        <div className="mt-auto flex flex-wrap gap-1 pt-1">
          <Badge title={formatLong(s.format)}>{formatShort(s.format)}</Badge>
          <Badge title="Duração alvo">{formatDuration(s.format.duration.target_s)}</Badge>
          {s.structure && s.structure !== 'none' ? <Badge title="Estrutura do roteiro">{label(STRUCTURE_LABELS, s.structure)}</Badge> : null}
          {item.warnings.length ? (
            <Badge tone="warn" icon={TriangleAlert} title={item.warnings.join('\n')}>
              {item.warnings.length === 1 ? '1 aviso' : `${item.warnings.length} avisos`}
            </Badge>
          ) : null}
        </div>
        {item.source === 'preset' ? null : (
          <Link
            to={`/estilos/${s.id}#formato`}
            aria-label={`Renomear “${s.name}”`}
            className="inline-flex w-fit items-center gap-1 rounded-sm text-xs font-medium text-muted underline-offset-2 hover:text-accent hover:underline"
          >
            <Pencil size={12} aria-hidden="true" />
            Renomear
          </Link>
        )}
        <div className="truncate text-xs text-subtle" title={familyTitle}>
          Visual: {familyLabel(s.family, metaLabel(s.family))}
        </div>
        {parentName ? (
          <div className="text-xs text-subtle [overflow-wrap:anywhere]">Baseado em {parentName}</div>
        ) : parentName === null ? (
          <div className="text-xs text-subtle">Baseado num estilo apagado</div>
        ) : null}
      </div>
    </Card>
  );
};

const CardGrid: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">{children}</div>
);

/* ------------------------------------------------------------------ */
/* Resultado de "criar a partir de uma descrição"                      */
/* ------------------------------------------------------------------ */

const GeneratedResult: React.FC<{
  result: Generated;
  baseName: string;
  providerName: string;
  saving: boolean;
  regenerating: boolean;
  /** Nome da base atual em "Partir de", quando ela é diferente da base usada nesta geração. */
  newBaseName: string | null;
  canRegenerate: boolean;
  onSave: () => void;
  onRegenerate: () => void;
  onDiscard: () => void;
}> = ({ result, baseName, providerName, saving, regenerating, newBaseName, canRegenerate, onSave, onRegenerate, onDiscard }) => {
  const groups = useMemo(() => {
    const map = new Map<string, FromText['applied']>();
    for (const change of result.applied) {
      const section = sectionOfPath(change.path);
      map.set(section, [...(map.get(section) ?? []), change]);
    }
    return [...map.entries()];
  }, [result.applied]);

  return (
    <div className="flex flex-col gap-4 rounded-lg border border-line-strong bg-raised p-4">
      <div className="flex flex-wrap items-start gap-3">
        <StyleThumb style={result.style} />
        <div className="min-w-0 flex-1">
          <div className="font-display text-[15px] font-semibold text-fg [overflow-wrap:anywhere]">{result.style.name}</div>
          {result.summary ? <p className="mt-1 text-sm text-muted">{result.summary}</p> : null}
          <p className="mt-1 text-xs text-subtle">
            Baseado em {baseName} · gerado por {providerName}
          </p>
        </div>
      </div>

      {groups.length ? (
        <div className="flex flex-col gap-3">
          <h3 className="text-sm font-medium text-fg">
            {result.applied.length === 1 ? '1 mudança' : `${result.applied.length} mudanças`} em relação a {baseName}
          </h3>
          {groups.map(([section, changes]) => (
            <div key={section} className="flex flex-col gap-1.5">
              <div className="text-xs font-semibold uppercase tracking-wide text-subtle">{section}</div>
              <ul className="flex flex-col gap-1.5">
                {changes.map((change, i) => {
                  const text = formatValue(change.path, change.value);
                  const colors = colorsIn(change.value);
                  return (
                    <li key={`${change.path}-${i}`} className="rounded-md border border-line bg-card px-3 py-2 text-[13px]">
                      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <span className="font-medium text-fg">{fieldLabel(change.path, result.style)}</span>
                        <span className="flex min-w-0 items-center gap-1.5 text-muted" title={text}>
                          {colors.map((c) => (
                            <ColorSwatch key={c} color={c} />
                          ))}
                          <span className="line-clamp-2 [overflow-wrap:anywhere]">{text}</span>
                        </span>
                      </div>
                      {change.reason ? <p className="mt-0.5 text-xs text-subtle">{change.reason}</p> : null}
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-sm text-muted">A IA não mudou nenhum campo: o estilo ficou igual a {baseName}, só com o nome e a descrição novos.</p>
      )}

      {result.rejected.length ? (
        <details className="rounded-md border border-amber-300/35 bg-amber-300/10 px-3 py-2 text-[13px]">
          <summary className="flex w-fit cursor-pointer items-center gap-1.5 rounded-sm font-medium text-amber-200">
            <TriangleAlert size={14} aria-hidden="true" />
            {result.rejected.length === 1 ? '1 mudança não aplicada' : `${result.rejected.length} mudanças não aplicadas`}
          </summary>
          <p className="mt-1.5 text-xs text-muted">A IA sugeriu estes valores, mas eles não passaram na validação do estilo e ficaram de fora.</p>
          <ul className="mt-2 flex flex-col gap-1">
            {result.rejected.map((r, i) => (
              <li key={`${r.path}-${i}`} className="text-muted">
                <span className="font-medium text-fg">
                  {sectionOfPath(r.path) === fieldLabel(r.path, result.style) ? sectionOfPath(r.path) : `${sectionOfPath(r.path)} · ${fieldLabel(r.path, result.style)}`}
                </span>
                <span className="block break-words text-xs text-subtle">{r.error}</span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        <Button variant="primary" icon={Save} busy={saving} disabled={regenerating} onClick={onSave}>
          Salvar como “{result.style.name}”
        </Button>
        <Button icon={RotateCw} busy={regenerating} disabled={saving || !canRegenerate} onClick={onRegenerate}>
          {newBaseName ? `Gerar de novo a partir de ${newBaseName}` : 'Gerar de novo'}
        </Button>
        <Button variant="ghost" icon={X} disabled={saving || regenerating} onClick={onDiscard}>
          Descartar
        </Button>
      </div>
      <p className="-mt-2 text-xs text-subtle">
        Nada é salvo até você clicar em Salvar. Gerar de novo usa a descrição, a base e o nome que estão no formulário acima e gasta mais um pedido da IA de
        roteiro (sua cota).
      </p>
    </div>
  );
};

/* ------------------------------------------------------------------ */
/* Página                                                              */
/* ------------------------------------------------------------------ */

export const StylesPage: React.FC = () => {
  const navigate = useNavigate();
  const client = useQueryClient();
  const { toast } = useToast();
  const [params, setParams] = useSearchParams();
  const styles = useQuery({ queryKey: ['styles'], queryFn: () => api.get<StyleItem[]>('/api/styles') });
  const meta = useQuery({ queryKey: ['styles-meta'], queryFn: () => api.get<Meta>('/api/styles/meta') });
  const providers = useProviders();
  const metaLabel = (id: string) => meta.data?.families.find((f) => f.id === id)?.label;

  const query = params.get('q') ?? '';
  const formatParam = params.get('formato');
  const format: FormatFilter = formatParam === 'curto' || formatParam === 'longo' ? formatParam : 'todos';
  const originParam = params.get('origem');
  const origin: OriginFilter = originParam === 'meus' || originParam === 'presets' ? originParam : 'todos';

  const setParam = (key: string, value: string | null) =>
    setParams(
      (prev) => {
        const p = new URLSearchParams(prev);
        if (value) p.set(key, value);
        else p.delete(key);
        return p;
      },
      { replace: true },
    );

  const [creating, setCreating] = useState(false);
  const [description, setDescription] = useState('');
  const [base, setBase] = useState(DEFAULT_BASE);
  const [name, setName] = useState('');
  const [result, setResult] = useState<Generated | null>(null);
  const [announcement, setAnnouncement] = useState('');
  const panelRef = useRef<HTMLDivElement>(null);
  const newButtonRef = useRef<HTMLButtonElement>(null);
  // Cada geração recebe um número: uma resposta que chega depois de fechar ou de outra geração é ignorada.
  const runId = useRef(0);
  const confirm = useConfirm();

  const all = styles.data ?? [];
  const byId = useMemo(() => new Map(all.map((i) => [i.style.id, i])), [all]);
  const takenIds = useMemo(() => all.map((i) => i.style.id), [all]);
  const nameOf = (id: string | null | undefined) => (id ? (byId.get(id)?.style.name ?? 'um estilo apagado') : '');

  // Se o preset padrão não existir, parte do primeiro estilo da lista.
  useEffect(() => {
    if (all.length && !byId.has(base)) setBase(all[0].style.id);
  }, [all, byId, base]);

  const generate = useMutation({
    mutationFn: (vars: { description: string; base: string; name: string }) =>
      api.post<FromText>('/api/styles/from-text', { description: vars.description, base_style_id: vars.base, name: vars.name || undefined }),
  });
  const save = useMutation({
    mutationFn: (vars: { style: StyleSpec; parent: string }) => api.post<{ style: StyleSpec }>('/api/styles', { style: vars.style, parent_id: vars.parent }),
    onSuccess: (data) => {
      client.invalidateQueries({ queryKey: ['styles'] });
      toast({ tone: 'good', title: 'Estilo criado', description: `“${data.style.name}” está em Meus estilos.` });
      navigate(`/estilos/${data.style.id}`);
    },
  });

  const canGenerate = description.trim().length >= 3 && !save.isPending;
  const runGenerate = () => {
    if (!canGenerate || generate.isPending) return;
    save.reset();
    const id = ++runId.current;
    setAnnouncement('');
    generate.mutate(
      { description: description.trim(), base, name: name.trim() },
      {
        onSuccess: (data, vars) => {
          if (id !== runId.current) return;
          setResult({ ...data, base: data.base_style_id ?? vars.base });
          const n = data.applied.length;
          setAnnouncement(`Estilo gerado: ${n === 1 ? '1 mudança pronta' : `${n} mudanças prontas`} para revisar.`);
        },
      },
    );
  };

  const saveResult = () => {
    if (!result) return;
    // O id padrão do backend ("<base>-custom") pode já existir e seria sobrescrito: escolhe um id livre a partir do nome.
    const id = takenIds.includes(result.style.id) ? suggestStyleId(result.style.name, takenIds) : result.style.id;
    save.mutate({ style: { ...result.style, id }, parent: result.base });
  };

  const openCreator = () => {
    setCreating(true);
    window.setTimeout(() => {
      panelRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      document.getElementById(DESCRIPTION_ID)?.focus({ preventScroll: true });
    }, 0);
  };

  /** Pergunta antes de jogar fora um resultado que já gastou cota. */
  const confirmDiscard = async () =>
    !result ||
    confirm({
      title: 'Descartar o estilo gerado?',
      description: 'A geração já usou um pedido da IA de roteiro e não dá para recuperar. Gerar de novo usa outro pedido.',
      confirmLabel: 'Descartar',
      tone: 'danger',
    });

  const closeCreator = async () => {
    if (!(await confirmDiscard())) return;
    runId.current += 1;
    setCreating(false);
    setResult(null);
    setAnnouncement('');
    generate.reset();
    save.reset();
    window.setTimeout(() => newButtonRef.current?.focus(), 0);
  };

  const discardResult = async () => {
    if (!(await confirmDiscard())) return;
    runId.current += 1;
    setResult(null);
    setAnnouncement('');
    save.reset();
    window.setTimeout(() => document.getElementById(DESCRIPTION_ID)?.focus(), 0);
  };

  const filtered = useMemo(() => {
    const q = normalize(query.trim());
    return all.filter((item) => {
      const s = item.style;
      if (format === 'curto' && s.format.type !== 'short') return false;
      if (format === 'longo' && s.format.type !== 'long') return false;
      if (!q) return true;
      return normalize(`${s.name} ${s.description} ${s.tags.join(' ')}`).includes(q);
    });
  }, [all, query, format]);

  const mine = filtered.filter((i) => i.source === 'user');
  const presets = filtered.filter((i) => i.source === 'preset');
  const totalMine = all.filter((i) => i.source === 'user').length;
  const filtersActive = Boolean(query.trim()) || format !== 'todos' || origin !== 'todos';
  const listFiltered = Boolean(query.trim()) || format !== 'todos';
  const showMine = origin !== 'presets';
  const showPresets = origin !== 'meus';
  const panelOpen = creating || Boolean(result) || generate.isPending;
  const providerName = (id: string) => providers.data?.find((p) => p.id === id)?.name ?? 'IA de roteiro';

  const clearFilters = () =>
    setParams(
      (prev) => {
        const p = new URLSearchParams(prev);
        p.delete('q');
        p.delete('formato');
        p.delete('origem');
        return p;
      },
      { replace: true },
    );

  const presetOptions = all.filter((i) => i.source === 'preset');
  const mineOptions = all.filter((i) => i.source === 'user');

  return (
    <Page>
      <StyleFonts />
      <PageHeader
        title="Estilos"
        description="Um estilo junta formato, roteiro, voz, arte, edição, áudio e thumbnail."
        actions={
          panelOpen ? null : (
            <Button ref={newButtonRef} variant="primary" icon={Plus} onClick={openCreator}>
              Novo estilo
            </Button>
          )
        }
      />

      {panelOpen ? (
        <div ref={panelRef} className="mb-8 scroll-mt-4">
          <Section
            icon={Sparkles}
            title="Criar estilo a partir de uma descrição"
            description="A IA de roteiro ajusta um estilo existente conforme o que você descrever. Você confere as mudanças antes de salvar."
            actions={
              <Button
                variant="ghost"
                size="sm"
                icon={X}
                onClick={closeCreator}
                disabled={generate.isPending || save.isPending}
                title={generate.isPending ? 'Espere a geração terminar' : undefined}
                aria-label="Fechar criação de estilo"
              >
                Fechar
              </Button>
            }
          >
            <Field label="Descreva o estilo" hint="Ex.: documentário de jornal antigo, lento, sépia, texto de máquina de escrever, legendas pequenas.">
              <Textarea
                id={DESCRIPTION_ID}
                rows={3}
                value={description}
                maxLength={2000}
                onChange={(e) => setDescription(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) runGenerate();
                }}
              />
            </Field>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Partir de" hint="O novo estilo começa igual a este e recebe só as mudanças descritas.">
                <Select value={base} onChange={(e) => setBase(e.target.value)}>
                  {mineOptions.length ? (
                    <optgroup label="Meus estilos">
                      {mineOptions.map((s) => (
                        <option key={s.style.id} value={s.style.id}>
                          {s.style.name}
                        </option>
                      ))}
                    </optgroup>
                  ) : null}
                  <optgroup label="Presets">
                    {presetOptions.map((s) => (
                      <option key={s.style.id} value={s.style.id}>
                        {s.style.name}
                      </option>
                    ))}
                  </optgroup>
                </Select>
              </Field>
              <Field label="Nome (opcional)" hint={`Sem nome, fica “${nameOf(base) || 'Estilo'} (personalizado)”.`}>
                <Input value={name} maxLength={120} onChange={(e) => setName(e.target.value)} />
              </Field>
            </div>
            {result ? null : (
              <div className="flex flex-wrap items-center justify-end gap-x-3 gap-y-2">
                <span className="flex items-center gap-1.5 text-xs text-subtle">
                  <Sparkles size={14} aria-hidden="true" />
                  Usa um pedido da IA de roteiro (sua cota).
                </span>
                <Button variant="primary" icon={Sparkles} busy={generate.isPending} disabled={!canGenerate} onClick={runGenerate}>
                  Gerar estilo
                </Button>
              </div>
            )}
            <span role="status" className="sr-only">
              {announcement}
            </span>
            <ErrorBox error={generate.error} title="Não foi possível gerar o estilo" onClose={() => generate.reset()} />
            <ErrorBox error={save.error} title="Não foi possível salvar o estilo" onClose={() => save.reset()} />
            {result ? (
              <GeneratedResult
                result={result}
                baseName={nameOf(result.base)}
                providerName={providerName(result.provider)}
                saving={save.isPending}
                regenerating={generate.isPending}
                newBaseName={base !== result.base ? nameOf(base) : null}
                canRegenerate={canGenerate}
                onSave={saveResult}
                onRegenerate={runGenerate}
                onDiscard={discardResult}
              />
            ) : null}
          </Section>
        </div>
      ) : null}

      <div className="mb-6 flex flex-wrap items-end gap-3">
        <div className="relative w-full sm:w-72">
          <Search size={16} aria-hidden="true" className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-subtle" />
          <Input
            type="search"
            aria-label="Buscar estilos por nome ou descrição"
            placeholder="Buscar por nome ou descrição"
            value={query}
            onChange={(e) => setParam('q', e.target.value || null)}
            className="w-full pl-8"
          />
        </div>
        <div className="flex flex-col gap-1">
          <span aria-hidden="true" className="text-xs font-medium text-subtle">
            Formato
          </span>
          <ChipGroup label="Filtrar por formato" options={FORMAT_FILTERS} value={format} onChange={(id) => setParam('formato', id === 'todos' ? null : id)} />
        </div>
        <div className="flex flex-col gap-1">
          <span aria-hidden="true" className="text-xs font-medium text-subtle">
            Origem
          </span>
          <ChipGroup label="Filtrar por origem" options={ORIGIN_FILTERS} value={origin} onChange={(id) => setParam('origem', id === 'todos' ? null : id)} />
        </div>
        {filtersActive ? (
          <Button variant="ghost" size="sm" icon={X} onClick={clearFilters}>
            Limpar filtros
          </Button>
        ) : null}
      </div>

      {styles.isLoading ? (
        <div role="status" aria-label="Carregando estilos">
          <CardGrid>
            {Array.from({ length: 6 }, (_, i) => (
              <Skeleton key={i} className="h-32 rounded-xl" />
            ))}
          </CardGrid>
        </div>
      ) : styles.error ? (
        <ErrorBox error={styles.error} title="Não foi possível carregar os estilos" onRetry={() => styles.refetch()} />
      ) : (
        <div className="flex flex-col gap-8">
          {showMine ? (
            <section aria-labelledby="estilos-meus" className="flex flex-col gap-3">
              <h2 id="estilos-meus" className="flex items-center gap-2 font-display text-base font-semibold text-fg">
                Meus estilos
                <span className="text-sm font-normal tabular-nums text-subtle">{listFiltered ? `${mine.length} de ${totalMine}` : totalMine}</span>
              </h2>
              {mine.length ? (
                <CardGrid>
                  {mine.map((item) => (
                    <StyleCard
                      key={item.style.id}
                      item={item}
                      metaLabel={metaLabel}
                      parentName={item.parent_id ? (byId.get(item.parent_id)?.style.name ?? null) : undefined}
                    />
                  ))}
                </CardGrid>
              ) : totalMine ? (
                <Empty icon={Search} title="Nenhum dos seus estilos combina com a busca" action={<Button size="sm" onClick={clearFilters}>Limpar filtros</Button>} />
              ) : showPresets && presetOptions.length ? (
                // Sem estilos próprios: uma linha só, para os presets (o ponto de partida) ficarem à vista.
                <p className="text-sm text-muted">
                  Você ainda não tem estilos próprios. Duplique um preset abaixo ou{' '}
                  {panelOpen ? (
                    'descreva um estilo no painel acima.'
                  ) : (
                    <>
                      <button type="button" onClick={openCreator} className="rounded-sm font-medium text-fg underline underline-offset-2 hover:text-accent">
                        descreva um estilo
                      </button>
                      .
                    </>
                  )}
                </p>
              ) : (
                <Empty
                  icon={Palette}
                  title="Você ainda não tem estilos próprios"
                  action={
                    panelOpen ? null : (
                      <Button icon={Sparkles} onClick={openCreator}>
                        Descrever um estilo
                      </Button>
                    )
                  }
                >
                  Abra um preset e clique em “Duplicar e editar”, ou descreva o estilo em texto para a IA montar.
                </Empty>
              )}
            </section>
          ) : null}

          {showPresets ? (
            <section aria-labelledby="estilos-presets" className="flex flex-col gap-3">
              <div>
                <h2 id="estilos-presets" className="flex items-center gap-2 font-display text-base font-semibold text-fg">
                  Presets
                  <span className="text-sm font-normal tabular-nums text-subtle">
                    {listFiltered ? `${presets.length} de ${presetOptions.length}` : presetOptions.length}
                  </span>
                </h2>
                <p className="mt-0.5 text-[13px] text-subtle">Estilos de fábrica, somente leitura. Duplique um para criar sua versão.</p>
              </div>
              {presets.length ? (
                <CardGrid>
                  {presets.map((item) => (
                    <StyleCard key={item.style.id} item={item} metaLabel={metaLabel} />
                  ))}
                </CardGrid>
              ) : (
                <Empty icon={Search} title="Nenhum preset combina com a busca" action={<Button size="sm" onClick={clearFilters}>Limpar filtros</Button>} />
              )}
            </section>
          ) : null}
        </div>
      )}
    </Page>
  );
};
