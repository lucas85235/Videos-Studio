// Acervo de material real do projeto (ADR 0004 §3.4 e §3.11), na aba Cenas: o `pool.json` coletado antes do
// storyboard, os links oficiais que o usuário cola (trailer, gameplay) e a troca de candidato: qualquer item do acervo
// vai para qualquer cena. "Trocar IA por real" (no índice da edição) abre este painel com as cenas de IA marcadas.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronDown, ChevronUp, Film, Image as ImageIcon, Library, Link2, RotateCw, X } from 'lucide-react';
import React, { useEffect, useId, useMemo, useState } from 'react';
import { fileUrl, type ProjectDetail } from '../../api';
import { type PoolItem, poolApi, PROV_LABELS } from '../../apiPool';
import { Badge, type BadgeTone, Button, ErrorBox, Input, Select, Skeleton, useToast } from '../../ui';
import { createKeyedStore, sceneChanges, useKeyedValue } from './core/drafts';
import { isActive, sceneNumbers } from './core/model';

/** Cenas que o usuário quer trocar de IA para real (vem do "Trocar IA por real"). Chave única: "cenas". */
export const poolTargets = createKeyedStore<string[]>();
const TARGETS_KEY = 'cenas';
const SHOWN = 12;

export const PROV_TONE: Record<string, BadgeTone> = {
  own: 'good',
  official: 'accent',
  capture: 'accent',
  free: 'good',
  stock: 'neutral',
  graphic: 'neutral',
  meme: 'warn',
  ai: 'bad',
};

const seconds = (v: number | null) => (v == null ? '' : `${v.toLocaleString('pt-BR', { maximumFractionDigits: 1 })} s`);

const ItemCard: React.FC<{
  item: PoolItem;
  usedBy: number[];
  scenes: { id: string; number: number; label: string }[];
  target: string;
  busy: boolean;
  onAttach: (sceneId: string) => void;
}> = ({ item, usedBy, scenes, target, busy, onAttach }) => {
  const [scene, setScene] = useState(target);
  useEffect(() => setScene(target), [target]);
  const thumb = item.poster ?? (item.media !== 'clip' ? item.path : null);
  const selectId = useId();
  return (
    <li className="flex min-w-0 flex-col gap-2 rounded-lg border border-line bg-canvas p-2">
      <div className="relative aspect-video overflow-hidden rounded-md bg-raised">
        {thumb ? (
          <img src={fileUrl(thumb)} alt="" loading="lazy" className="h-full w-full object-cover" />
        ) : (
          <span className="flex h-full items-center justify-center text-subtle">
            {item.media === 'clip' ? <Film size={20} aria-hidden="true" /> : <ImageIcon size={20} aria-hidden="true" />}
          </span>
        )}
        <span className="absolute left-1 top-1 flex gap-1">
          <Badge tone={PROV_TONE[item.tier] ?? 'neutral'}>{PROV_LABELS[item.tier]}</Badge>
          {item.dur_s ? <Badge>{seconds(item.dur_s)}</Badge> : null}
        </span>
      </div>
      <p className="line-clamp-2 text-[13px] text-fg" title={item.desc}>
        <span className="text-subtle">{item.id} · </span>
        {item.desc || 'sem descrição'}
      </p>
      {item.credit || item.license ? (
        <p className="truncate text-xs text-subtle" title={[item.credit, item.license].filter(Boolean).join(' · ')}>
          {[item.credit, item.license].filter(Boolean).join(' · ')}
        </p>
      ) : null}
      {usedBy.length ? <p className="text-xs text-emerald-300">Na cena {usedBy.join(', ')}</p> : null}
      <div className="mt-auto flex items-center gap-1.5">
        <label htmlFor={selectId} className="sr-only">
          Cena que recebe {item.id}
        </label>
        <Select id={selectId} value={scene} onChange={(e) => setScene(e.target.value)} className="min-w-0 flex-1 py-1 text-xs">
          <option value="">Usar na cena…</option>
          {scenes.map((s) => (
            <option key={s.id} value={s.id}>
              {s.label}
            </option>
          ))}
        </Select>
        <Button size="sm" disabled={!scene || busy} onClick={() => onAttach(scene)}>
          Usar
        </Button>
      </div>
    </li>
  );
};

export const PoolPanel: React.FC<{ detail: ProjectDetail; disabled?: boolean; onShowScene: (sceneId: string) => void }> = ({
  detail,
  disabled,
  onShowScene,
}) => {
  const { doc } = detail;
  const client = useQueryClient();
  const { toast } = useToast();
  const bodyId = useId();
  const linkId = useId();
  const targets = useKeyedValue(poolTargets, doc.id, TARGETS_KEY) ?? [];
  const [open, setOpen] = useState(false);
  const [all, setAll] = useState(false);
  const [link, setLink] = useState('');
  const gathering = detail.jobs.find((j) => j.kind === 'media_pool' && isActive(j));
  const key = ['project', doc.id, 'pool'];
  const query = useQuery({ queryKey: key, queryFn: () => poolApi.get(doc.id), refetchInterval: gathering ? 4000 : false });
  const refresh = () => client.invalidateQueries({ queryKey: key });

  // "Trocar IA por real" abre o painel e rola até ele.
  const expanded = open || targets.length > 0;
  useEffect(() => {
    if (targets.length) document.getElementById(bodyId)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, [targets.length, bodyId]);
  // O job da coleta terminou: busca o acervo novo.
  const [wasGathering, setWasGathering] = useState(Boolean(gathering));
  useEffect(() => {
    if (wasGathering && !gathering) void refresh();
    setWasGathering(Boolean(gathering));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gathering]);

  const numbers = useMemo(() => sceneNumbers(doc), [doc]);
  const scenes = useMemo(
    () =>
      doc.scenes
        .filter((s) => !s.locked)
        .map((s) => ({ id: s.id, number: numbers.get(s.id) ?? 0, label: `Cena ${numbers.get(s.id) ?? '?'}${targets.includes(s.id) ? ' (IA)' : ''}` }))
        .sort((a, b) => a.number - b.number),
    [doc, numbers, targets],
  );
  const usedBy = useMemo(() => {
    const out = new Map<string, number[]>();
    for (const s of doc.scenes) {
      const pid = s.visual.media?.pool_id;
      if (pid) out.set(pid, [...(out.get(pid) ?? []), numbers.get(s.id) ?? 0].sort((a, b) => a - b));
    }
    return out;
  }, [doc, numbers]);

  const links = query.data?.links ?? [];
  const addLinks = useMutation({
    mutationFn: (urls: string[]) => poolApi.setLinks(doc.id, urls.map((url) => ({ url }))),
    onSuccess: async () => {
      setLink('');
      await refresh();
      toast({ tone: 'info', title: 'Link guardado', description: 'Clique em Coletar de novo para baixar e cortar os planos.' });
    },
  });
  const removeLink = useMutation({
    mutationFn: (url: string) =>
      poolApi.setLinks(
        doc.id,
        links.filter((l) => l.url !== url),
        true,
      ),
    onSuccess: () => refresh(),
  });
  const gather = useMutation({
    mutationFn: () => poolApi.refresh(doc.id),
    onSuccess: () => client.invalidateQueries({ queryKey: ['project', doc.id] }),
  });
  const attach = useMutation({
    mutationFn: ({ sceneId, poolId }: { sceneId: string; poolId: string }) => poolApi.attach(doc.id, sceneId, poolId),
    onSuccess: async (result, { sceneId, poolId }) => {
      sceneChanges.set(doc.id, sceneId, Date.now());
      poolTargets.set(
        doc.id,
        TARGETS_KEY,
        targets.filter((id) => id !== sceneId),
      );
      await client.invalidateQueries({ queryKey: ['project', doc.id] });
      toast({
        tone: result.warnings.length ? 'warn' : 'good',
        title: `${poolId} na cena ${numbers.get(sceneId) ?? ''}`,
        description: result.warnings.join(' ') || 'Gere a prévia de novo para ver.',
      });
      onShowScene(sceneId);
    },
  });

  const data = query.data;
  const items = data?.pool.items ?? [];
  const shown = all ? items : items.slice(0, SHOWN);
  const byTier = Object.entries(data?.summary.by_tier ?? {}).sort((a, b) => (b[1] ?? 0) - (a[1] ?? 0));
  const submitLink = () => {
    const urls = link
      .split(/\s+/)
      .map((u) => u.trim())
      .filter(Boolean);
    if (urls.length) addLinks.mutate(urls);
  };

  return (
    <section aria-label="Acervo de material real" className="rounded-xl border border-line bg-card">
      <header className="flex flex-wrap items-start gap-x-3 gap-y-2 px-4 py-3">
        <Library size={16} aria-hidden="true" className="mt-0.5 shrink-0 text-muted" />
        <div className="min-w-0 flex-1 basis-64">
          <h2 className="font-display text-[15px] font-semibold leading-6 text-fg">Acervo de material real</h2>
          <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[13px] text-muted">
            {query.isPending ? (
              <Skeleton className="h-4 w-40" />
            ) : items.length ? (
              <>
                <span>{items.length === 1 ? '1 item' : `${items.length} itens`}</span>
                {byTier.map(([tier, n]) => (
                  <Badge key={tier} tone={PROV_TONE[tier] ?? 'neutral'}>
                    {PROV_LABELS[tier as keyof typeof PROV_LABELS] ?? tier} {n}
                  </Badge>
                ))}
              </>
            ) : (
              <span>Vazio: envie fotos e vídeos do projeto, cole links oficiais ou capture as fontes, e colete.</span>
            )}
            {gathering ? <span className="text-fg">· {gathering.message || 'Coletando…'}</span> : null}
          </div>
          {targets.length ? (
            <p className="mt-1 text-[13px] text-amber-200">
              Escolha material real para {targets.length === 1 ? 'a cena' : 'as cenas'} {targets.map((id) => numbers.get(id) ?? '?').join(', ')} (hoje com IA).
            </p>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            icon={RotateCw}
            busy={gather.isPending || Boolean(gathering)}
            disabled={disabled || Boolean(gathering)}
            title="Baixa os links novos, captura as fontes e busca por tópico."
            onClick={() => gather.mutate()}
          >
            Coletar de novo
          </Button>
          <Button
            size="sm"
            variant="ghost"
            icon={expanded ? ChevronUp : ChevronDown}
            aria-expanded={expanded}
            aria-controls={bodyId}
            onClick={() => {
              if (expanded) {
                setOpen(false);
                poolTargets.set(doc.id, TARGETS_KEY, null);
              } else setOpen(true);
            }}
          >
            {expanded ? 'Esconder' : 'Ver o acervo'}
          </Button>
        </div>
      </header>
      <div id={bodyId} hidden={!expanded} className="@container flex scroll-mt-4 flex-col gap-4 border-t border-line px-4 py-3">
        <div className="flex flex-col gap-2">
          <label htmlFor={linkId} className="text-sm font-medium text-fg">
            Links oficiais (trailer, gameplay, vídeo do canal)
          </label>
          <div className="flex flex-wrap gap-2">
            <Input
              id={linkId}
              value={link}
              placeholder="https://www.youtube.com/watch?v=…"
              onChange={(e) => setLink(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  submitLink();
                }
              }}
              className="min-w-0 flex-1 basis-72"
            />
            <Button size="sm" icon={Link2} busy={addLinks.isPending} disabled={!link.trim()} onClick={submitLink}>
              Adicionar
            </Button>
          </div>
          <p className="text-xs text-subtle">
            Material oficial entra mudo, em planos curtos e com crédito. Pode gerar reivindicação de Content ID: use só o que você pode usar.
          </p>
          {links.length ? (
            <ul className="flex flex-col gap-1">
              {links.map((l) => (
                <li key={l.url} className="flex items-center gap-2 text-[13px]">
                  <Badge tone={PROV_TONE[l.tier]}>{PROV_LABELS[l.tier]}</Badge>
                  <a href={l.url} target="_blank" rel="noreferrer" className="min-w-0 truncate text-muted hover:text-fg">
                    {l.label || l.url}
                  </a>
                  <Button size="sm" variant="ghost" icon={X} aria-label={`Tirar o link ${l.url}`} busy={removeLink.isPending && removeLink.variables === l.url} onClick={() => removeLink.mutate(l.url)} />
                </li>
              ))}
            </ul>
          ) : null}
          <ErrorBox error={addLinks.error ?? removeLink.error} title="Não foi possível guardar o link" onClose={() => (addLinks.reset(), removeLink.reset())} />
        </div>
        {data?.pool.warnings.length ? (
          <ul className="flex flex-col gap-0.5 text-xs text-amber-200">
            {data.pool.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        ) : null}
        {query.isError ? <ErrorBox error={query.error} title="Não foi possível ler o acervo" onRetry={() => query.refetch()} /> : null}
        {items.length ? (
          <>
            <ul className="grid grid-cols-1 gap-3 @xl:grid-cols-2 @4xl:grid-cols-3 @6xl:grid-cols-4">
              {shown.map((item) => (
                <ItemCard
                  key={item.id}
                  item={item}
                  usedBy={usedBy.get(item.id) ?? []}
                  scenes={scenes}
                  target={targets[0] ?? ''}
                  busy={attach.isPending || Boolean(disabled)}
                  onAttach={(sceneId) => attach.mutate({ sceneId, poolId: item.id })}
                />
              ))}
            </ul>
            {items.length > SHOWN ? (
              <Button size="sm" variant="ghost" onClick={() => setAll(!all)}>
                {all ? 'Mostrar menos' : `Mostrar todos (${items.length})`}
              </Button>
            ) : null}
          </>
        ) : null}
        <ErrorBox error={gather.error ?? attach.error} title={attach.error ? 'Não foi possível usar o item' : 'Não foi possível coletar'} onClose={() => (gather.reset(), attach.reset())} />
        {data?.pool.gathered_at ? <p className="text-xs text-subtle">Coletado em {new Date(data.pool.gathered_at).toLocaleString('pt-BR')}.</p> : null}
      </div>
    </section>
  );
};
